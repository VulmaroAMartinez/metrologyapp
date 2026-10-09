"""API de gestión de usuarios (/api/v1/usuarios/): solo ADMIN, sin hashes, sin borrado físico
y sin desbloqueo manual."""

import json
import logging
from datetime import timedelta
from unittest import mock
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
CLAVE = "tornillo-azul-77-nube"
CLAVE_2 = "nube-de-algodon-88-azul"
ADMIN, TECNICO, ESPECTADOR = Usuario.Rol.ADMIN, Usuario.Rol.TECNICO, Usuario.Rol.ESPECTADOR
CAMPOS = {
    "id",
    "nombre",
    "correo",
    "numero_nomina",
    "rol",
    "activo",
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
    assert cuerpo["count"] == 31
    assert len(cuerpo["results"]) == 25
    assert cuerpo["next"]
    assert cuerpo["previous"] is None
    assert all(set(fila) == CAMPOS for fila in cuerpo["results"])


def test_pagination_walks_through_every_user_exactly_once(api_admin):
    crear_muchos(30)

    ids = []
    for pagina in (1, 2):
        ids += [u["id"] for u in api_admin.get(f"{BASE}?pagina={pagina}").json()["results"]]

    assert len(ids) == 31
    assert len(set(ids)) == 31


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
    assert respuesta.status_code == 200
    assert set(cuerpo) == CAMPOS
    assert cuerpo["bloqueado_hasta"] is not None
    assert cuerpo["numero_nomina"] == "X1"
    assert "intentos_fallidos" not in cuerpo
    assert "password" not in cuerpo
    assert respuesta["Cache-Control"] == "no-store"


def test_detail_of_an_unknown_user_is_404(api_admin):
    respuesta = api_admin.get(ruta(987654))

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "no_encontrado"


# --- crear ---


def nuevo(**extra):
    return {
        "nombre": "Luis Pérez",
        "correo": "luis@empresa.test",
        "rol": "TECNICO",
        "contrasena": CLAVE,
        **extra,
    }


def test_create_stores_the_password_the_admin_wrote_and_never_returns_it(api_admin):
    respuesta = api_admin.post(BASE, nuevo(numero_nomina=" N-7 "))

    cuerpo = respuesta.json()
    assert respuesta.status_code == 201
    assert respuesta["Cache-Control"] == "no-store"
    assert set(cuerpo) == CAMPOS  # ningún campo de contraseña, ni temporal ni de otro tipo
    assert cuerpo["activo"] is True
    assert cuerpo["numero_nomina"] == "N-7"
    assert cuerpo["bloqueado_hasta"] is None
    assert CLAVE not in respuesta.content.decode()
    for texto in (api_admin.get(ruta(cuerpo["id"])).content, api_admin.get(BASE).content):
        assert CLAVE.encode() not in texto


def test_the_created_user_logs_in_straight_away_with_the_password_the_admin_wrote(api_admin):
    api_admin.post(BASE, nuevo())

    respuesta = Api().login("luis@empresa.test", CLAVE)

    assert respuesta.status_code == 200
    assert set(respuesta.json()) == {"id", "nombre", "correo", "rol", "numero_nomina"}


@pytest.mark.parametrize(
    "campo", ["password", "debe_cambiar_contrasena", "contrasena_temporal", "activo", "id"]
)
def test_create_rejects_fields_that_are_not_part_of_the_form(api_admin, campo):
    respuesta = api_admin.post(BASE, nuevo(**{campo: "x"}))

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert Usuario.objects.count() == 1


def test_create_requires_the_password(api_admin):
    cuerpo = nuevo()
    del cuerpo["contrasena"]

    respuesta = api_admin.post(BASE, cuerpo)

    assert respuesta.status_code == 400
    assert "contrasena" in respuesta.json()["detalle"]


@pytest.mark.parametrize(
    ("contrasena", "fragmento"),
    [
        ("Ab1-cdef9", "10 caracteres"),
        ("qwertyuiop", "muy común"),
        ("48291746503", "numérica"),
        ("luis@empresa.test1", "muy similar"),
    ],
)
def test_create_runs_the_password_through_the_validators(api_admin, contrasena, fragmento):
    respuesta = api_admin.post(BASE, nuevo(contrasena=contrasena))

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert fragmento in respuesta.json()["detalle"]
    assert contrasena not in respuesta.content.decode()  # el mensaje no repite la contraseña
    assert Usuario.objects.count() == 1
    assert not Evento.objects.filter(tipo="USUARIO_CREADO").exists()


def test_an_overlong_password_is_rejected_before_reaching_the_hasher(api_admin):
    respuesta = api_admin.post(BASE, nuevo(contrasena="a1" * 200))

    assert respuesta.status_code == 400


def test_blank_nomina_is_stored_as_null_and_the_name_is_trimmed(api_admin):
    cuerpo = api_admin.post(BASE, nuevo(nombre="  Luis  ", numero_nomina="   ")).json()

    assert cuerpo["numero_nomina"] is None
    assert cuerpo["nombre"] == "Luis"


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"nombre": "", "correo": "a@e.test", "rol": "TECNICO", "contrasena": CLAVE},
        {"nombre": "   ", "correo": "a@e.test", "rol": "TECNICO", "contrasena": CLAVE},
        {"nombre": "x" * 151, "correo": "a@e.test", "rol": "TECNICO", "contrasena": CLAVE},
        {"nombre": "A", "correo": "no-es-correo", "rol": "TECNICO", "contrasena": CLAVE},
        {"nombre": "A", "correo": "a@e.test", "rol": "SUPERADMIN", "contrasena": CLAVE},
        {"nombre": "A", "correo": "a@e.test", "contrasena": CLAVE},
        {
            "nombre": "A",
            "correo": "a@e.test",
            "rol": "TECNICO",
            "contrasena": CLAVE,
            "numero_nomina": "9" * 21,
        },
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
    ],
)
def test_edit_rejects_fields_that_have_their_own_endpoint_or_are_read_only(api_admin, campo):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    respuesta = api_admin.call("PATCH", ruta(objetivo.pk), {campo: "x"})

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"


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

    assert respuesta.status_code == 200
    assert respuesta.json()["rol"] == "ESPECTADOR"
    assert (
        api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {"rol": "SUPERADMIN"}).status_code == 400
    )
    assert api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {}).status_code == 400
    assert api_admin.post(ruta(987654, "cambiar-rol"), {"rol": "ADMIN"}).status_code == 404


def test_an_admin_cannot_change_their_role_deactivate_or_reset_themselves(api_admin, admin):
    clave_antes = admin.password
    rol = api_admin.post(ruta(admin.pk, "cambiar-rol"), {"rol": "TECNICO"})
    desactivar = api_admin.post(ruta(admin.pk, "desactivar"))
    restablecer = api_admin.post(ruta(admin.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2})

    for respuesta in (rol, desactivar, restablecer):
        assert respuesta.status_code == 403
        assert respuesta.json()["codigo"] == "auto_modificacion_no_permitida"
    admin.refresh_from_db()
    assert admin.rol == ADMIN
    assert admin.activo is True
    assert admin.password == clave_antes
    assert admin.check_password(DEFAULT_PASSWORD)
    assert api_admin.get(ME).status_code == 200  # su sesión sigue intacta
    assert Evento.objects.filter(tipo__startswith="USUARIO_").count() == 0


def test_deactivate_and_reactivate(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    desactivado = api_admin.post(ruta(objetivo.pk, "desactivar")).json()
    assert Api().login(objetivo.correo).status_code == 401  # inactivo no entra
    reactivado = api_admin.post(ruta(objetivo.pk, "reactivar")).json()

    assert desactivado["activo"] is False
    assert reactivado["activo"] is True
    assert Api().login(objetivo.correo).status_code == 200
    assert Usuario.objects.filter(pk=objetivo.pk).exists()  # nunca hay borrado físico


def test_there_is_no_delete_and_no_unlock_endpoint(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    assert api_admin.call("DELETE", ruta(objetivo.pk)).status_code == 405
    assert api_admin.call("PUT", ruta(objetivo.pk), nuevo()).status_code == 405
    assert api_admin.post(ruta(objetivo.pk, "desbloquear")).status_code == 404
    assert Usuario.objects.filter(pk=objetivo.pk).exists()


# --- restablecer contraseña ---


def reset(api, pk, contrasena=CLAVE_2):
    return api.post(ruta(pk, "restablecer-contrasena"), {"contrasena": contrasena})


def test_reset_sets_the_password_the_admin_wrote_and_returns_no_password(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    respuesta = reset(api_admin, objetivo.pk)

    assert respuesta.status_code == 200
    assert respuesta["Cache-Control"] == "no-store"
    assert set(respuesta.json()) == CAMPOS
    assert CLAVE_2 not in respuesta.content.decode()
    assert Api().login(objetivo.correo, DEFAULT_PASSWORD).status_code == 401
    assert Api().login(objetivo.correo, CLAVE_2).status_code == 200  # entra sin cambio forzado


def test_reset_requires_the_password_and_runs_the_validators(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    sin_clave = api_admin.post(ruta(objetivo.pk, "restablecer-contrasena"), {})
    debil = reset(api_admin, objetivo.pk, "qwertyuiop")
    similar = reset(api_admin, objetivo.pk, "b@empresa.test1")

    assert sin_clave.status_code == 400
    assert debil.status_code == 400
    assert "muy común" in debil.json()["detalle"]
    assert similar.status_code == 400
    assert "qwertyuiop" not in debil.content.decode()
    assert Api().login(objetivo.correo, DEFAULT_PASSWORD).status_code == 200  # no cambió


def test_reset_rejects_unknown_fields_and_unknown_users(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    extra = api_admin.post(
        ruta(objetivo.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2, "activo": True}
    )

    assert extra.status_code == 400
    assert reset(api_admin, 987654).status_code == 404


def test_a_body_that_is_not_an_object_is_rejected_with_400(api_admin):
    respuesta = api_admin.post(BASE, ["nombre", "correo"])

    assert respuesta.status_code == 400
    assert Usuario.objects.count() == 1  # solo el administrador de la sesión


def test_an_admin_resets_the_other_admins_password_without_reauthentication(api_admin):
    otro = UsuarioFactory(correo="admin2@empresa.test", rol=ADMIN)
    sesion_otro = sesion(otro)

    respuesta = reset(api_admin, otro.pk)  # solo manda la contraseña nueva del afectado

    assert respuesta.status_code == 200
    assert sesion_otro.get(ME).status_code == 401  # sus sesiones se cerraron
    assert Api().login(otro.correo, CLAVE_2).status_code == 200
    evento = Evento.objects.get(tipo="USUARIO_CONTRASENA_RESTABLECIDA")
    assert evento.usuario.correo == "admin@empresa.test"
    assert "admin2@empresa.test" in evento.descripcion


def test_reset_does_not_unlock_a_locked_account(api_admin, clock):
    """Restablecer NO modifica intentos_fallidos ni bloqueado_hasta."""
    bloqueo = clock.now + timedelta(minutes=10)
    objetivo = UsuarioFactory(
        correo="bloqueada@empresa.test", intentos_fallidos=5, bloqueado_hasta=bloqueo
    )

    reset(api_admin, objetivo.pk)

    objetivo.refresh_from_db()
    assert (objetivo.intentos_fallidos, objetivo.bloqueado_hasta) == (5, bloqueo)
    assert Api().login(objetivo.correo, CLAVE_2).status_code == 401  # sigue bloqueada
    assert api_admin.get(ruta(objetivo.pk)).json()["bloqueado_hasta"] is not None
    clock.advance(minutes=10, seconds=1)
    assert Api().login(objetivo.correo, CLAVE_2).status_code == 200  # se libera solo al vencer


# --- sesiones ---


def test_after_a_role_change_the_open_session_has_the_new_permissions_on_the_next_request(
    api_admin,
):
    otro_admin = UsuarioFactory(correo="admin2@empresa.test", rol=ADMIN)
    sesion_otro = sesion(otro_admin)
    assert sesion_otro.get(BASE).status_code == 200

    api_admin.post(ruta(otro_admin.pk, "cambiar-rol"), {"rol": "ESPECTADOR"})

    denegada = sesion_otro.get(BASE)
    assert denegada.status_code == 403
    assert denegada.json()["codigo"] == "permiso_denegado"
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
        assert respuesta.status_code == 401
        assert respuesta.json()["codigo"] == "no_autenticado"


def test_resetting_closes_the_open_sessions_immediately(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")
    uno, dos = sesion(objetivo), sesion(objetivo)

    reset(api_admin, objetivo.pk)

    assert uno.get(ME).status_code == 401
    assert dos.get(ME).status_code == 401
    assert not Session.objects.filter(session_key__in=[uno.session_cookie, dos.session_cookie])


# --- flujo completo ---


def test_full_flow_the_admin_assigns_passwords_and_the_technician_cannot_change_hers(api_admin):
    creado = api_admin.post(BASE, nuevo(nombre="Nuevo Técnico", correo="tecnico@empresa.test"))
    assert creado.status_code == 201

    tecnico = Api()
    entrada = tecnico.login("tecnico@empresa.test", CLAVE)  # entra directo: no hay cambio forzado
    assert entrada.status_code == 200
    assert entrada.json()["rol"] == "TECNICO"
    assert tecnico.get(ME).status_code == 200
    assert tecnico.get(BASE).json()["codigo"] == "permiso_denegado"  # no administra

    # No puede cambiar su contraseña por ninguna vía de la API.
    cambio = tecnico.post(CAMBIAR, {"contrasena_actual": CLAVE, "contrasena_nueva": CLAVE_2})
    assert cambio.status_code == 403
    assert cambio.json()["codigo"] == "permiso_denegado"
    assert Api().login("tecnico@empresa.test", CLAVE).status_code == 200  # nada cambió

    # Solo el administrador la cambia, y la sesión del técnico se cierra.
    reset(api_admin, creado.json()["id"])
    assert tecnico.get(ME).status_code == 401
    assert Api().login("tecnico@empresa.test", CLAVE).status_code == 401
    assert Api().login("tecnico@empresa.test", CLAVE_2).status_code == 200


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
        ("POST", ruta(pk, "restablecer-contrasena"), {"contrasena": CLAVE_2}),
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
    llamar("POST", ruta(bloqueada.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2})
    llamar("POST", BASE, nuevo())  # duplicado -> error
    llamar("GET", ruta(987654))  # 404
    llamar("GET", f"{BASE}?orden=password")  # 400
    llamar("GET", ME)

    assert len(cuerpos) == 14
    for cuerpo in cuerpos:
        assert CLAVE not in cuerpo
        assert CLAVE_2 not in cuerpo
        for valor in valores(json.loads(cuerpo)):
            assert not (isinstance(valor, str) and "argon2$" in valor), cuerpo
            assert valor not in {"password", "intentos_fallidos", "last_login"}, cuerpo


# --- las contraseñas escritas no aparecen en respuestas, eventos ni logs ---


def test_passwords_never_reach_responses_events_or_logs_after_each_operation(api_admin, caplog):
    caplog.set_level(logging.DEBUG)
    objetivo = UsuarioFactory(correo="b@empresa.test")
    secretos = [CLAVE, CLAVE_2, "qwertyuiop", "otra-clave-larga-55"]
    respuestas = []

    def comprobar():
        texto = json.dumps(list(Evento.objects.values()), default=str) + caplog.text
        texto += "".join(respuestas)
        for secreto in secretos:
            assert secreto not in texto, secreto

    respuestas.append(api_admin.post(BASE, nuevo()).content.decode())
    comprobar()
    respuestas.append(reset(api_admin, objetivo.pk).content.decode())
    comprobar()
    # errores: validadores, duplicado, usuario inexistente, campo no permitido
    respuestas.append(
        api_admin.post(BASE, nuevo(correo="c@e.test", contrasena="qwertyuiop")).content.decode()
    )
    respuestas.append(api_admin.post(BASE, nuevo()).content.decode())  # correo duplicado
    respuestas.append(reset(api_admin, 987654, "otra-clave-larga-55").content.decode())
    respuestas.append(reset(api_admin, objetivo.pk, "qwertyuiop").content.decode())
    api_admin.call("PATCH", ruta(objetivo.pk), {"nombre": "Nuevo Nombre"})
    api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {"rol": "ESPECTADOR"})
    api_admin.post(ruta(objetivo.pk, "desactivar"))
    api_admin.post(ruta(objetivo.pk, "reactivar"))
    comprobar()


def test_a_failure_in_the_middle_never_leaks_the_password_to_the_client_or_the_logs(admin, caplog):
    caplog.set_level(logging.DEBUG)
    api = Api(raise_request_exception=False)
    api.client.force_login(admin)
    api.csrf()
    objetivo = UsuarioFactory(correo="b@empresa.test")

    with mock.patch.object(services, "registrar_evento", side_effect=RuntimeError("falla")):
        crear_fallido = api.post(BASE, nuevo(correo="otro@empresa.test"))
        reset_fallido = reset(api, objetivo.pk)

    assert crear_fallido.status_code == 500
    assert reset_fallido.status_code == 500
    for secreto in (CLAVE, CLAVE_2):
        assert secreto not in crear_fallido.content.decode() + reset_fallido.content.decode()
        assert secreto not in caplog.text
