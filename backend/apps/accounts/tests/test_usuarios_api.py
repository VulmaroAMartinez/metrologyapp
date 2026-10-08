"""API de gestión de usuarios (/api/v1/usuarios/): solo ADMIN, sin hashes, sin borrado físico
y sin desbloqueo manual."""

import json
import logging
from datetime import timedelta
from urllib.parse import quote

import pytest
from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Usuario
from apps.audit.models import Evento
from config.settings import base

from .conftest import CAMBIAR, ME, Api
from .factories import DEFAULT_PASSWORD, UsuarioFactory

pytestmark = pytest.mark.django_db

BASE = "/api/v1/usuarios/"
ADMIN, TECNICO, ESPECTADOR = Usuario.Rol.ADMIN, Usuario.Rol.TECNICO, Usuario.Rol.ESPECTADOR
CAMPOS = {
    "id",
    "nombre",
    "correo",
    "numero_nomina",
    "rol",
    "activo",
    "debe_cambiar_contrasena",
    "creado_en",
    "bloqueado_hasta",
}


def ruta(pk, accion=""):
    return f"{BASE}{pk}/{accion + '/' if accion else ''}"


@pytest.fixture
def admin(db):
    return UsuarioFactory(correo="admin@empresa.test", nombre="Admin Uno", rol=ADMIN)


@pytest.fixture
def api_admin(admin, relaxed_rate):
    api = Api()
    assert api.login(admin.correo).status_code == 200
    return api


def sesion(usuario):
    api = Api()
    assert api.login(usuario.correo).status_code == 200
    return api


@pytest.fixture
def real_hasher(settings):
    settings.PASSWORD_HASHERS = base.PASSWORD_HASHERS
    services.dummy_hash.cache_clear()
    yield
    services.dummy_hash.cache_clear()


def valores(objeto):
    """Todas las claves y valores de un JSON, a cualquier profundidad."""
    if isinstance(objeto, dict):
        for clave, valor in objeto.items():
            yield clave
            yield from valores(valor)
    elif isinstance(objeto, list):
        for elemento in objeto:
            yield from valores(elemento)
    else:
        yield objeto


# --- listado: paginación, filtros, búsqueda y orden ----------------------------------------------


def crear_muchos(n, **extra):
    Usuario.objects.bulk_create(
        [
            Usuario(
                correo=f"masivo{i:03}@empresa.test",
                nombre=f"Masivo {i:03}",
                rol=extra.get("rol", TECNICO),
                password="!",
                activo=extra.get("activo", True),
            )
            for i in range(n)
        ]
    )


def test_list_shape_and_default_page_size(api_admin):
    crear_muchos(30)

    respuesta = api_admin.get(BASE)

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200
    assert set(cuerpo) == {"count", "next", "previous", "results"}
    assert cuerpo["count"] == 31 and len(cuerpo["results"]) == 25
    assert cuerpo["next"] and cuerpo["previous"] is None
    assert all(set(fila) == CAMPOS for fila in cuerpo["results"])


def test_pagination_walks_through_every_user_exactly_once(api_admin):
    crear_muchos(30)

    ids = []
    for pagina in (1, 2):
        ids += [u["id"] for u in api_admin.get(f"{BASE}?pagina={pagina}").json()["results"]]

    assert len(ids) == 31 and len(set(ids)) == 31


def test_page_size_is_configurable_and_capped_at_100(api_admin):
    crear_muchos(110)

    assert len(api_admin.get(f"{BASE}?tamano=10").json()["results"]) == 10
    assert len(api_admin.get(f"{BASE}?tamano=100").json()["results"]) == 100
    assert len(api_admin.get(f"{BASE}?tamano=1000").json()["results"]) == 100  # tope


@pytest.mark.parametrize("tamano", ["0", "-5", "abc", ""])
def test_invalid_page_size_falls_back_to_the_default(api_admin, tamano):
    crear_muchos(30)

    assert len(api_admin.get(f"{BASE}?tamano={tamano}").json()["results"]) == 25


def test_a_page_out_of_range_is_a_clean_404(api_admin):
    respuesta = api_admin.get(f"{BASE}?pagina=99")

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "no_encontrado"


def test_filter_by_role_and_active(api_admin):
    UsuarioFactory(correo="t1@empresa.test", rol=TECNICO)
    UsuarioFactory(correo="t2@empresa.test", rol=TECNICO, activo=False)
    UsuarioFactory(correo="e1@empresa.test", rol=ESPECTADOR)

    def correos(consulta):
        return {u["correo"] for u in api_admin.get(f"{BASE}?{consulta}").json()["results"]}

    assert correos("rol=TECNICO") == {"t1@empresa.test", "t2@empresa.test"}
    assert correos("rol=TECNICO&activo=true") == {"t1@empresa.test"}
    assert correos("activo=false") == {"t2@empresa.test"}
    assert correos("activo=0") == {"t2@empresa.test"}
    assert correos("rol=ADMIN") == {"admin@empresa.test"}
    assert len(correos("")) == 4


@pytest.mark.parametrize(
    "consulta", ["rol=SUPERADMIN", "activo=quizas", "orden=password", "orden=intentos_fallidos"]
)
def test_invalid_filters_are_400_and_cannot_sort_by_sensitive_columns(api_admin, consulta):
    respuesta = api_admin.get(f"{BASE}?{consulta}")

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"


def test_search_by_name_email_and_nomina_ignoring_case(api_admin):
    UsuarioFactory(correo="lucia@empresa.test", nombre="Lucía Gómez", numero_nomina="N-0042")
    UsuarioFactory(correo="otro@empresa.test", nombre="Otro Nombre")

    def buscar(texto):
        respuesta = api_admin.get(f"{BASE}?buscar={quote(texto)}")
        return {u["correo"] for u in respuesta.json()["results"]}

    assert buscar("GÓMEZ") == {"lucia@empresa.test"}
    assert buscar("lucia@") == {"lucia@empresa.test"}
    assert buscar("n-0042") == {"lucia@empresa.test"}
    assert buscar("no-existe") == set()
    assert buscar("otro") == {"otro@empresa.test"}


def test_search_treats_wildcards_literally(api_admin):
    UsuarioFactory(correo="a@empresa.test", nombre="Ana")

    assert api_admin.get(f"{BASE}?buscar=%25").json()["count"] == 0  # "%" no es comodín


def test_ordering(api_admin):
    UsuarioFactory(correo="b@empresa.test", nombre="Beto")
    UsuarioFactory(correo="a@empresa.test", nombre="Zeta")

    def nombres(orden):
        return [u["nombre"] for u in api_admin.get(f"{BASE}?orden={orden}").json()["results"]]

    assert nombres("nombre") == sorted(nombres("nombre"))
    assert nombres("-nombre") == sorted(nombres("-nombre"), reverse=True)
    assert nombres("correo")[0] == "Zeta"  # a@empresa.test va antes que admin@ y b@
    assert api_admin.get(f"{BASE}?orden=-creado_en").status_code == 200


# --- detalle y forma de las respuestas ---


def test_detail_shows_the_public_fields_and_read_only_lock_date(api_admin):
    bloqueo = timezone.now() + timedelta(minutes=10)
    objetivo = UsuarioFactory(
        correo="b@empresa.test", intentos_fallidos=4, bloqueado_hasta=bloqueo, numero_nomina="X1"
    )

    respuesta = api_admin.get(ruta(objetivo.pk))

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200 and set(cuerpo) == CAMPOS
    assert cuerpo["bloqueado_hasta"] is not None and cuerpo["numero_nomina"] == "X1"
    assert "intentos_fallidos" not in cuerpo and "password" not in cuerpo
    assert respuesta["Cache-Control"] == "no-store"


def test_detail_of_an_unknown_user_is_404(api_admin):
    respuesta = api_admin.get(ruta(987654))

    assert respuesta.status_code == 404 and respuesta.json()["codigo"] == "no_encontrado"


# --- crear ---


def nuevo(**extra):
    return {"nombre": "Luis Pérez", "correo": "luis@empresa.test", "rol": "TECNICO", **extra}


def test_create_returns_the_temporary_password_once_with_no_store(api_admin, admin):
    respuesta = api_admin.post(BASE, nuevo(numero_nomina=" N-7 "))

    cuerpo = respuesta.json()
    assert respuesta.status_code == 201
    assert respuesta["Cache-Control"] == "no-store"
    assert set(cuerpo) == CAMPOS | {"contrasena_temporal"}
    assert len(cuerpo["contrasena_temporal"]) >= 16
    assert cuerpo["debe_cambiar_contrasena"] is True and cuerpo["activo"] is True
    assert cuerpo["numero_nomina"] == "N-7" and cuerpo["bloqueado_hasta"] is None
    # No vuelve a poder consultarse.
    for texto in (
        api_admin.get(ruta(cuerpo["id"])).content,
        api_admin.get(BASE).content,
    ):
        assert cuerpo["contrasena_temporal"].encode() not in texto
        assert b"contrasena_temporal" not in texto


def test_the_created_user_can_log_in_with_the_temporary_password(api_admin):
    temporal = api_admin.post(BASE, nuevo()).json()["contrasena_temporal"]

    respuesta = Api().login("luis@empresa.test", temporal)

    assert respuesta.status_code == 200 and respuesta.json()["debe_cambiar_contrasena"] is True


def test_the_administrator_cannot_choose_the_password_or_other_fields(api_admin):
    for campo in ("contrasena", "password", "debe_cambiar_contrasena", "activo", "id"):
        respuesta = api_admin.post(BASE, nuevo(**{campo: "x"}))

        assert respuesta.status_code == 400, campo
        assert respuesta.json()["codigo"] == "datos_invalidos"
    assert Usuario.objects.count() == 1


def test_blank_nomina_is_stored_as_null_and_the_name_is_trimmed(api_admin):
    cuerpo = api_admin.post(BASE, nuevo(nombre="  Luis  ", numero_nomina="   ")).json()

    assert cuerpo["numero_nomina"] is None and cuerpo["nombre"] == "Luis"


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"nombre": "", "correo": "a@e.test", "rol": "TECNICO"},
        {"nombre": "   ", "correo": "a@e.test", "rol": "TECNICO"},
        {"nombre": "x" * 151, "correo": "a@e.test", "rol": "TECNICO"},
        {"nombre": "A", "correo": "no-es-correo", "rol": "TECNICO"},
        {"nombre": "A", "correo": "a@e.test", "rol": "SUPERADMIN"},
        {"nombre": "A", "correo": "a@e.test"},
        {"nombre": "A", "correo": "a@e.test", "rol": "TECNICO", "numero_nomina": "9" * 21},
    ],
)
def test_invalid_create_bodies_are_400(api_admin, cuerpo):
    respuesta = api_admin.post(BASE, cuerpo)

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert Evento.objects.filter(tipo="USUARIO_CREADO").count() == 0


@pytest.mark.parametrize(
    "correo", ["luis@empresa.test", "LUIS@Empresa.TEST", " luis@empresa.test "]
)
def test_duplicate_email_is_a_clear_400_never_a_500(api_admin, correo):
    api_admin.post(BASE, nuevo())

    respuesta = api_admin.post(BASE, nuevo(correo=correo, nombre="Otro"))

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "correo_duplicado"
    assert Usuario.objects.filter(correo="luis@empresa.test").count() == 1


@pytest.mark.parametrize("nomina", ["N-7", "n-7", "  N-7  "])
def test_duplicate_nomina_is_a_clear_400(api_admin, nomina):
    api_admin.post(BASE, nuevo(numero_nomina="N-7"))

    respuesta = api_admin.post(BASE, nuevo(correo="otro@empresa.test", numero_nomina=nomina))

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "numero_nomina_duplicado"


# --- editar ---


def test_edit_updates_and_normalizes(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test", nombre="Beto")

    respuesta = api_admin.call(
        "PATCH",
        ruta(objetivo.pk),
        {"nombre": " Beto Ruiz ", "correo": " BETO.R@Empresa.test ", "numero_nomina": " 55 "},
    )

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200
    assert (cuerpo["nombre"], cuerpo["correo"], cuerpo["numero_nomina"]) == (
        "Beto Ruiz",
        "beto.r@empresa.test",
        "55",
    )
    assert Evento.objects.get(tipo="USUARIO_EDITADO").usuario.correo == "admin@empresa.test"


def test_edit_can_clear_the_nomina(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test", numero_nomina="55")

    cuerpo = api_admin.call("PATCH", ruta(objetivo.pk), {"numero_nomina": ""}).json()

    assert cuerpo["numero_nomina"] is None


@pytest.mark.parametrize(
    "campo",
    [
        "rol",
        "activo",
        "password",
        "contrasena",
        "bloqueado_hasta",
        "intentos_fallidos",
        "debe_cambiar_contrasena",
    ],
)
def test_edit_rejects_fields_that_have_their_own_endpoint_or_are_read_only(api_admin, campo):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    respuesta = api_admin.call("PATCH", ruta(objetivo.pk), {campo: "x"})

    assert respuesta.status_code == 400 and respuesta.json()["codigo"] == "datos_invalidos"


def test_edit_duplicates_and_unknown_user(api_admin):
    uno = UsuarioFactory(correo="uno@empresa.test", numero_nomina="U-1")
    dos = UsuarioFactory(correo="dos@empresa.test")

    por_correo = api_admin.call("PATCH", ruta(dos.pk), {"correo": "UNO@empresa.test"})
    por_nomina = api_admin.call("PATCH", ruta(dos.pk), {"numero_nomina": " u-1 "})
    inexistente = api_admin.call("PATCH", ruta(987654), {"nombre": "X"})

    assert (por_correo.status_code, por_correo.json()["codigo"]) == (400, "correo_duplicado")
    assert (por_nomina.status_code, por_nomina.json()["codigo"]) == (400, "numero_nomina_duplicado")
    assert inexistente.status_code == 404
    assert uno.pk != dos.pk


def test_an_empty_edit_changes_nothing_and_writes_no_event(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    assert api_admin.call("PATCH", ruta(objetivo.pk), {}).status_code == 200
    assert Evento.objects.filter(tipo="USUARIO_EDITADO").count() == 0


# --- rol, desactivar, reactivar ---


def test_change_role(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test", rol=TECNICO)

    respuesta = api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {"rol": "ESPECTADOR"})

    assert respuesta.status_code == 200 and respuesta.json()["rol"] == "ESPECTADOR"
    assert (
        api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {"rol": "SUPERADMIN"}).status_code == 400
    )
    assert api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {}).status_code == 400
    assert api_admin.post(ruta(987654, "cambiar-rol"), {"rol": "ADMIN"}).status_code == 404


def test_an_admin_cannot_change_their_own_role_or_deactivate_themselves(api_admin, admin):
    rol = api_admin.post(ruta(admin.pk, "cambiar-rol"), {"rol": "TECNICO"})
    desactivar = api_admin.post(ruta(admin.pk, "desactivar"))

    for respuesta in (rol, desactivar):
        assert respuesta.status_code == 403
        assert respuesta.json()["codigo"] == "auto_modificacion_no_permitida"
    admin.refresh_from_db()
    assert admin.rol == ADMIN and admin.activo is True


def test_deactivate_and_reactivate(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    desactivado = api_admin.post(ruta(objetivo.pk, "desactivar")).json()
    assert Api().login(objetivo.correo).status_code == 401  # inactivo no entra
    reactivado = api_admin.post(ruta(objetivo.pk, "reactivar")).json()

    assert desactivado["activo"] is False and reactivado["activo"] is True
    assert Api().login(objetivo.correo).status_code == 200
    assert Usuario.objects.filter(pk=objetivo.pk).exists()  # nunca hay borrado físico


def test_there_is_no_delete_and_no_unlock_endpoint(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    assert api_admin.call("DELETE", ruta(objetivo.pk)).status_code == 405
    assert api_admin.call("PUT", ruta(objetivo.pk), nuevo()).status_code == 405
    assert api_admin.post(ruta(objetivo.pk, "desbloquear")).status_code == 404
    assert Usuario.objects.filter(pk=objetivo.pk).exists()


# --- restablecer contraseña ---


def test_reset_returns_a_temporary_password_once(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    respuesta = api_admin.post(ruta(objetivo.pk, "restablecer-contrasena"))

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200 and respuesta["Cache-Control"] == "no-store"
    assert len(cuerpo["contrasena_temporal"]) >= 16 and cuerpo["debe_cambiar_contrasena"] is True
    assert b"contrasena_temporal" not in api_admin.get(ruta(objetivo.pk)).content
    assert Api().login(objetivo.correo, DEFAULT_PASSWORD).status_code == 401
    assert Api().login(objetivo.correo, cuerpo["contrasena_temporal"]).status_code == 200


def test_reset_does_not_unlock_a_locked_account(api_admin, clock):
    """Punto 4: restablecer NO modifica intentos_fallidos ni bloqueado_hasta."""
    bloqueo = clock.now + timedelta(minutes=10)
    objetivo = UsuarioFactory(
        correo="bloqueada@empresa.test", intentos_fallidos=5, bloqueado_hasta=bloqueo
    )

    temporal = api_admin.post(ruta(objetivo.pk, "restablecer-contrasena")).json()[
        "contrasena_temporal"
    ]

    objetivo.refresh_from_db()
    assert (objetivo.intentos_fallidos, objetivo.bloqueado_hasta) == (5, bloqueo)
    assert Api().login(objetivo.correo, temporal).status_code == 401  # sigue bloqueada
    assert api_admin.get(ruta(objetivo.pk)).json()["bloqueado_hasta"] is not None
    clock.advance(minutes=10, seconds=1)
    entrada = Api().login(objetivo.correo, temporal)  # se libera solo al vencer
    assert entrada.status_code == 200 and entrada.json()["debe_cambiar_contrasena"] is True


def test_an_admin_resetting_their_own_password_still_gets_the_temporary_one(api_admin, admin):
    respuesta = api_admin.post(ruta(admin.pk, "restablecer-contrasena"))

    assert respuesta.status_code == 200  # la sesión actual no se borra a mitad de petición
    temporal = respuesta.json()["contrasena_temporal"]
    assert api_admin.get(ME).status_code == 401  # pero deja de valer: cambió la contraseña
    assert Api().login(admin.correo, temporal).status_code == 200


# --- sesiones ---


def test_after_a_role_change_the_open_session_has_the_new_permissions_on_the_next_request(
    api_admin,
):
    otro_admin = UsuarioFactory(correo="admin2@empresa.test", rol=ADMIN)
    sesion_otro = sesion(otro_admin)
    assert sesion_otro.get(BASE).status_code == 200

    api_admin.post(ruta(otro_admin.pk, "cambiar-rol"), {"rol": "ESPECTADOR"})

    denegada = sesion_otro.get(BASE)
    assert denegada.status_code == 403 and denegada.json()["codigo"] == "permiso_denegado"
    assert sesion_otro.get(ME).json()["rol"] == "ESPECTADOR"  # la sesión sigue; el rol cambió

    api_admin.post(ruta(otro_admin.pk, "cambiar-rol"), {"rol": "ADMIN"})
    assert sesion_otro.get(BASE).status_code == 200


def test_deactivating_closes_the_open_sessions_immediately(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")
    uno, dos = sesion(objetivo), sesion(objetivo)
    assert uno.get(ME).status_code == 200

    api_admin.post(ruta(objetivo.pk, "desactivar"))

    for abierta in (uno, dos):
        respuesta = abierta.get(ME)
        assert respuesta.status_code == 401 and respuesta.json()["codigo"] == "no_autenticado"
    assert not Session.objects.filter(session_key__in=[uno.session_cookie, dos.session_cookie])


def test_resetting_closes_the_open_sessions_immediately(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")
    uno, dos = sesion(objetivo), sesion(objetivo)

    api_admin.post(ruta(objetivo.pk, "restablecer-contrasena"))

    assert uno.get(ME).status_code == 401 and dos.get(ME).status_code == 401
    assert not Session.objects.filter(session_key__in=[uno.session_cookie, dos.session_cookie])


# --- flujo completo ---


def test_full_flow_create_log_in_with_temporary_change_password_and_work(api_admin):
    creado = api_admin.post(
        BASE, nuevo(nombre="Nueva Admin", correo="nueva@empresa.test", rol="ADMIN")
    )
    temporal = creado.json()["contrasena_temporal"]

    nueva = Api()
    entrada = nueva.login("nueva@empresa.test", temporal)
    assert entrada.status_code == 200 and entrada.json()["debe_cambiar_contrasena"] is True

    # 403 en todo salvo me, logout y cambiar-contrasena
    for metodo, destino in [
        ("GET", BASE),
        ("POST", BASE),
        ("GET", ruta(1)),
        ("POST", ruta(1, "desactivar")),
    ]:
        bloqueada = nueva.call(metodo, destino, {} if metodo == "POST" else None)
        assert bloqueada.status_code == 403, destino
        assert bloqueada.json()["codigo"] == "debe_cambiar_contrasena", destino
    assert nueva.get(ME).status_code == 200

    cambio = nueva.post(
        CAMBIAR, {"contrasena_actual": temporal, "contrasena_nueva": "tornillo-azul-77-nube"}
    )
    assert cambio.status_code == 200 and cambio.json()["debe_cambiar_contrasena"] is False

    assert nueva.get(BASE).status_code == 200  # ya es administradora y puede trabajar
    assert nueva.post("/api/v1/auth/logout/").status_code == 204
    assert Api().login("nueva@empresa.test", "tornillo-azul-77-nube").status_code == 200
    assert Api().login("nueva@empresa.test", temporal).status_code == 401  # la temporal murió


# --- técnico y espectador: 403 en todo ---


def todos_los_endpoints(pk):
    return [
        ("GET", BASE, None),
        ("POST", BASE, nuevo(correo="x@empresa.test")),
        ("GET", ruta(pk), None),
        ("PATCH", ruta(pk), {"nombre": "Hackeado"}),
        ("POST", ruta(pk, "cambiar-rol"), {"rol": "ADMIN"}),
        ("POST", ruta(pk, "desactivar"), {}),
        ("POST", ruta(pk, "reactivar"), {}),
        ("POST", ruta(pk, "restablecer-contrasena"), {}),
    ]


@pytest.mark.parametrize("rol", [TECNICO, ESPECTADOR])
def test_technicians_and_spectators_get_403_on_every_endpoint(rol, admin):
    objetivo = UsuarioFactory(correo="victima@empresa.test", rol=TECNICO)
    api = sesion(UsuarioFactory(correo="intruso@empresa.test", rol=rol))
    antes = (Usuario.objects.count(), Evento.objects.count())
    instantanea = list(Usuario.objects.order_by("pk").values())

    for metodo, destino, cuerpo in todos_los_endpoints(objetivo.pk):
        respuesta = api.call(metodo, destino, cuerpo)

        assert respuesta.status_code == 403, (metodo, destino)
        assert respuesta.json()["codigo"] == "permiso_denegado"
    assert (Usuario.objects.count(), Evento.objects.count()) == antes
    assert list(Usuario.objects.order_by("pk").values()) == instantanea


def test_anonymous_gets_401_on_every_endpoint(admin):
    api = Api()
    api.csrf()

    for metodo, destino, cuerpo in todos_los_endpoints(admin.pk):
        assert api.call(metodo, destino, cuerpo).status_code == 401, (metodo, destino)


# --- ninguna respuesta contiene hashes ---


def test_no_response_ever_contains_a_password_hash(real_hasher, admin):
    """Con el hasher REAL (Argon2): recorre todas las respuestas JSON y falla si cualquier valor
    empieza con «argon2$» (o aparece una columna de hash o de contador)."""
    api = Api()
    assert api.login(admin.correo).status_code == 200
    bloqueada = UsuarioFactory(
        correo="bloqueada@empresa.test",
        intentos_fallidos=5,
        bloqueado_hasta=timezone.now() + timedelta(minutes=5),
    )
    assert bloqueada.password.startswith("argon2$")  # el hash existe y es real
    cuerpos = []

    def llamar(metodo, destino, datos=None):
        respuesta = api.call(metodo, destino, datos)
        cuerpos.append(respuesta.content.decode())
        return respuesta

    creado = llamar("POST", BASE, nuevo()).json()
    llamar("GET", BASE)
    llamar("GET", f"{BASE}?tamano=100&orden=-creado_en&buscar=lu")
    llamar("GET", ruta(creado["id"]))
    llamar("GET", ruta(bloqueada.pk))
    llamar("PATCH", ruta(creado["id"]), {"nombre": "Luis P."})
    llamar("POST", ruta(creado["id"], "cambiar-rol"), {"rol": "ESPECTADOR"})
    llamar("POST", ruta(creado["id"], "desactivar"))
    llamar("POST", ruta(creado["id"], "reactivar"))
    llamar("POST", ruta(bloqueada.pk, "restablecer-contrasena"))
    llamar("POST", BASE, nuevo())  # duplicado -> error
    llamar("GET", ruta(987654))  # 404
    llamar("GET", f"{BASE}?orden=password")  # 400
    llamar("GET", ME)

    assert len(cuerpos) == 14
    for cuerpo in cuerpos:
        for valor in valores(json.loads(cuerpo)):
            assert not (isinstance(valor, str) and "argon2$" in valor), cuerpo
            assert valor not in {"password", "intentos_fallidos", "last_login"}, cuerpo


# --- la contraseña temporal no aparece en eventos, logs ni errores ---


def test_temporary_passwords_never_reach_events_or_logs_after_each_operation(api_admin, caplog):
    caplog.set_level(logging.DEBUG)
    objetivo = UsuarioFactory(correo="b@empresa.test")
    temporales = []

    def comprobar():
        texto = json.dumps(list(Evento.objects.values()), default=str) + caplog.text
        for temporal in temporales:
            assert temporal not in texto

    temporales.append(api_admin.post(BASE, nuevo()).json()["contrasena_temporal"])
    comprobar()
    temporales.append(
        api_admin.post(ruta(objetivo.pk, "restablecer-contrasena")).json()["contrasena_temporal"]
    )
    comprobar()
    api_admin.post(BASE, nuevo())  # error de duplicado
    api_admin.call("PATCH", ruta(objetivo.pk), {"nombre": "Nuevo Nombre"})
    api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {"rol": "ESPECTADOR"})
    api_admin.post(ruta(objetivo.pk, "desactivar"))
    api_admin.post(ruta(objetivo.pk, "reactivar"))
    comprobar()
    assert len(temporales) == 2 and all(len(t) >= 16 for t in temporales)
