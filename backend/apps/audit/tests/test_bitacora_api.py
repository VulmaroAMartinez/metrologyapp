"""GET /api/v1/bitacora/eventos/: solo lectura, solo ADMIN, paginación por cursor y filtros."""

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts import services as cuentas
from apps.accounts.models import Usuario
from apps.accounts.tests.conftest import CAMBIAR, Api
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UsuarioFactory
from apps.audit.models import Evento, TipoEvento
from apps.audit.sanitize import CAMPOS_PERMITIDOS
from config.settings import base

pytestmark = pytest.mark.django_db

URL = "/api/v1/bitacora/eventos/"
ADMIN, TECNICO, ESPECTADOR = Usuario.Rol.ADMIN, Usuario.Rol.TECNICO, Usuario.Rol.ESPECTADOR
CAMPOS = {
    "id",
    "ocurrido_en",
    "ocurrido_en_local",
    "usuario",
    "tipo",
    "descripcion",
    "antes",
    "despues",
    "ip",
    "instrumento_id",
}
INICIO = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def admin(db):
    return UsuarioFactory(correo="admin@empresa.test", nombre="Admin Uno", rol=ADMIN)


def sesion_directa(usuario) -> Api:
    """Sesión abierta SIN pasar por el login auditado: no deja eventos propios en la bitácora,
    así cada prueba controla exactamente qué eventos existen."""
    api = Api()
    api.client.force_login(usuario)
    api.csrf()
    return api


@pytest.fixture
def api_admin(admin):
    return sesion_directa(admin)


@pytest.fixture
def relaxed_rate(settings):
    settings.LOGIN_RATE_PER_IP_EMAIL = settings.LOGIN_RATE_PER_IP = 1000


def sembrar(n, *, inicio=INICIO, paso=timedelta(minutes=1), **extra):
    """n eventos de más antiguo a más reciente; devuelve la lista creada."""
    datos = {"tipo": TipoEvento.SESION_INICIADA, "descripcion": "prueba", **extra}
    return Evento.objects.bulk_create(
        [Evento(ocurrido_en=inicio + paso * i, **datos) for i in range(n)]
    )


def pedir(api, consulta=""):
    return api.get(f"{URL}?{consulta}" if consulta else URL)


def recorrer(api, consulta="", entre_paginas=None):
    """Sigue `next` hasta el final; devuelve (eventos, número de páginas)."""
    resultados, paginas, ruta = [], 0, f"{URL}?{consulta}" if consulta else URL
    while ruta:
        respuesta = api.get(ruta)
        assert respuesta.status_code == 200, respuesta.content
        cuerpo = respuesta.json()
        resultados += cuerpo["results"]
        paginas += 1
        if entre_paginas:
            entre_paginas(paginas)
        siguiente = cuerpo["next"]
        ruta = f"{urlparse(siguiente).path}?{urlparse(siguiente).query}" if siguiente else None
    return resultados, paginas


def ids(eventos):
    return [e["id"] for e in eventos]


# --- acceso y métodos -------------------------------------------------------------------------


def test_admin_reads_events_with_the_documented_shape(api_admin, admin):
    sembrar(3, usuario=admin, ip="203.0.113.7", instrumento_id=9, antes={"rol": "A"})

    respuesta = pedir(api_admin)

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200
    assert respuesta["Cache-Control"] == "no-store"
    assert set(cuerpo) == {"next", "results"}
    assert cuerpo["next"] is None
    evento = cuerpo["results"][0]
    assert set(evento) == CAMPOS
    assert evento["usuario"] == {"id": admin.pk, "nombre": "Admin Uno"}
    assert (evento["ip"], evento["instrumento_id"], evento["antes"]) == (
        "203.0.113.7",
        9,
        {"rol": "A"},
    )
    assert evento["ocurrido_en"] == "2026-10-01T12:02:00Z"  # UTC
    assert evento["ocurrido_en_local"] == "2026-10-01T06:02:00-06:00"  # America/Mexico_City


def test_system_events_have_a_null_user(api_admin):
    sembrar(1)

    assert pedir(api_admin).json()["results"][0]["usuario"] is None


@pytest.mark.parametrize("metodo", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_endpoint_is_read_only(api_admin, metodo):
    sembrar(2)

    respuesta = api_admin.call(metodo, URL, {"descripcion": "alterada"})

    assert respuesta.status_code == 405
    assert respuesta.json()["codigo"] == "metodo_no_permitido"
    assert Evento.objects.filter(descripcion="alterada").count() == 0


@pytest.mark.parametrize("rol", [TECNICO, ESPECTADOR])
@pytest.mark.parametrize("metodo", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_technicians_and_spectators_get_403(rol, metodo):
    api = sesion_directa(UsuarioFactory(correo="otro@empresa.test", rol=rol))
    sembrar(2)

    respuesta = api.call(metodo, URL, {} if metodo != "GET" else None)

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "permiso_denegado"


def test_anonymous_gets_401():
    assert Api().get(URL).status_code == 401


def test_reading_the_log_writes_no_event(api_admin):
    sembrar(5)
    antes = Evento.objects.count()

    pedir(api_admin)
    pedir(api_admin, "tipo=SESION_INICIADA&desde=2026-10-01")

    assert Evento.objects.count() == antes


# --- paginación por cursor ---


def test_order_is_newest_first_and_page_size_defaults_to_25(api_admin):
    sembrar(60)

    cuerpo = pedir(api_admin).json()

    assert len(cuerpo["results"]) == 25
    assert cuerpo["next"]
    marcas = [e["ocurrido_en"] for e in cuerpo["results"]]
    assert marcas == sorted(marcas, reverse=True)


def test_page_size_is_configurable_capped_at_100_and_falls_back_when_invalid(api_admin):
    sembrar(130)

    assert len(pedir(api_admin, "tamano=10").json()["results"]) == 10
    assert len(pedir(api_admin, "tamano=100").json()["results"]) == 100
    assert len(pedir(api_admin, "tamano=1000").json()["results"]) == 100
    for invalido in ("0", "-3", "abc", ""):
        assert len(pedir(api_admin, f"tamano={invalido}").json()["results"]) == 25


def test_walking_every_page_has_no_duplicates_and_no_gaps(api_admin):
    creados = sembrar(103)

    eventos, paginas = recorrer(api_admin, "tamano=10")

    esperado = [e.pk for e in sorted(creados, key=lambda e: (e.ocurrido_en, e.pk), reverse=True)]
    assert ids(eventos) == esperado  # todos, una vez, en el orden (ocurrido_en, id) descendente
    assert paginas == 11


def test_events_with_the_same_timestamp_are_ordered_by_id_without_losses(api_admin):
    creados = sembrar(40, paso=timedelta(0))  # 40 eventos en el mismo instante

    eventos, _ = recorrer(api_admin, "tamano=7")

    assert ids(eventos) == sorted((e.pk for e in creados), reverse=True)


def test_new_events_arriving_between_pages_cause_no_duplicates_or_gaps(api_admin):
    originales = sembrar(30)
    nuevos = []

    def llegan_eventos(pagina):
        if pagina == 1:  # entre la página 1 y la 2
            nuevos.extend(sembrar(5, inicio=INICIO + timedelta(days=1)))
            # y otro con el mismo instante que el último ya entregado (id mayor)
            nuevos.extend(sembrar(1, inicio=originales[-1].ocurrido_en - timedelta(minutes=0)))

    eventos, _ = recorrer(api_admin, "tamano=8", entre_paginas=llegan_eventos)

    vistos = ids(eventos)
    assert len(vistos) == len(set(vistos))  # sin duplicados
    assert {e.pk for e in originales} <= set(vistos)  # sin omisiones de lo que ya existía
    assert not (
        {e.pk for e in nuevos} & set(vistos[8:])
    )  # lo nuevo no se cuela a mitad de recorrido


def test_the_next_page_query_has_no_count_and_no_offset(api_admin):
    sembrar(30)
    siguiente = pedir(api_admin, "tamano=10").json()["next"]

    with CaptureQueriesContext(connection) as consultas:
        respuesta = api_admin.get(f"{urlparse(siguiente).path}?{urlparse(siguiente).query}")

    sql = " ".join(q["sql"] for q in consultas)
    assert respuesta.status_code == 200
    assert not re.search(r"\bCOUNT\b|\bOFFSET\b", sql, re.IGNORECASE)
    assert "LIMIT 11" in sql  # una fila de más para saber si hay otra página


@pytest.mark.parametrize(
    "cursor",
    [
        "no-es-base64!!",
        "e30=",  # {}
        "bnVsbA==",  # null
        "eyJ0IjoiZWwtZGlhIiwiaSI6MX0=",  # fecha ilegible
        "eyJ0IjoiMjAyNi0xMC0wMVQxMjowMDowMCIsImkiOjF9",  # fecha sin zona horaria
        "eyJ0IjoiMjAyNi0xMC0wMVQxMjowMDowMCswMDowMCIsImkiOi0xfQ==",  # id negativo
        "eyJ0IjoiMjAyNi0xMC0wMVQxMjowMDowMCswMDowMCIsImkiOiJ4In0=",  # id no entero
    ],
)
def test_malformed_cursors_are_400_never_500(api_admin, cursor):
    respuesta = pedir(api_admin, f"cursor={cursor}")

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"


# --- filtros ---


def test_filter_system_events_and_by_user(api_admin, admin):
    otro = UsuarioFactory(correo="otro@empresa.test")
    sembrar(3, usuario=None)
    sembrar(2, usuario=admin, inicio=INICIO + timedelta(days=1))
    sembrar(4, usuario=otro, inicio=INICIO + timedelta(days=2))

    assert len(recorrer(api_admin, "usuario=sistema")[0]) == 3
    assert len(recorrer(api_admin, "usuario=SISTEMA")[0]) == 3
    assert {e["usuario"] for e in recorrer(api_admin, "usuario=sistema")[0]} == {None}
    assert len(recorrer(api_admin, f"usuario={otro.pk}")[0]) == 4
    assert {e["usuario"]["id"] for e in recorrer(api_admin, f"usuario={admin.pk}")[0]} == {admin.pk}
    assert recorrer(api_admin, "usuario=987654")[0] == []


def test_filter_by_one_or_several_types(api_admin):
    sembrar(3, tipo=TipoEvento.SESION_INICIADA)
    sembrar(2, tipo=TipoEvento.SESION_FALLIDA, inicio=INICIO + timedelta(days=1))
    sembrar(4, tipo=TipoEvento.CUENTA_BLOQUEADA, inicio=INICIO + timedelta(days=2))

    def tipos(consulta):
        return {e["tipo"] for e in recorrer(api_admin, consulta)[0]}

    assert tipos("tipo=SESION_FALLIDA") == {"SESION_FALLIDA"}
    assert tipos("tipo=SESION_FALLIDA&tipo=CUENTA_BLOQUEADA") == {
        "SESION_FALLIDA",
        "CUENTA_BLOQUEADA",
    }
    assert tipos("tipo=SESION_FALLIDA,CUENTA_BLOQUEADA") == {"SESION_FALLIDA", "CUENTA_BLOQUEADA"}
    assert len(recorrer(api_admin, "tipo=SESION_FALLIDA,SESION_FALLIDA")[0]) == 2
    assert len(recorrer(api_admin)[0]) == 9


def test_filter_by_instrument_id(api_admin):
    sembrar(2, instrumento_id=5)
    sembrar(3, instrumento_id=6, inicio=INICIO + timedelta(days=1))
    sembrar(1, inicio=INICIO + timedelta(days=2))

    assert len(recorrer(api_admin, "instrumento_id=5")[0]) == 2
    assert {e["instrumento_id"] for e in recorrer(api_admin, "instrumento_id=6")[0]} == {6}


def test_filters_combine(api_admin, admin):
    sembrar(5, usuario=admin, tipo=TipoEvento.SESION_FALLIDA)
    sembrar(5, usuario=None, tipo=TipoEvento.SESION_FALLIDA, inicio=INICIO + timedelta(days=1))
    sembrar(5, usuario=admin, tipo=TipoEvento.SESION_INICIADA, inicio=INICIO + timedelta(days=2))

    eventos = recorrer(
        api_admin, f"usuario={admin.pk}&tipo=SESION_FALLIDA&desde=2026-10-01&hasta=2026-10-01"
    )[0]

    assert len(eventos) == 5


@pytest.mark.parametrize(
    "consulta",
    [
        "desde=2026-13-45",
        "desde=2026-02-30",
        "desde=abc",
        "hasta=ayer",
        "desde=2026-10-05&hasta=2026-10-01",
        "usuario=abc",
        "usuario=0",
        "usuario=-3",
        "usuario=1.5",
        "tipo=INVENTADO",
        "tipo=SESION_FALLIDA,INVENTADO",
        "instrumento_id=abc",
        "instrumento_id=0",
        "instrumento_id=-1",
    ],
)
def test_invalid_parameters_are_400_with_a_clear_code(api_admin, consulta):
    respuesta = pedir(api_admin, consulta)

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert set(respuesta.json()) == {"codigo", "detalle"}


# --- rango de fechas: límites del día local y cambio de horario ---


def en(texto):
    return datetime.fromisoformat(texto)


def sembrar_en(*instantes):
    return {
        t: Evento.objects.create(tipo="SESION_INICIADA", descripcion="x", ocurrido_en=en(t))
        for t in instantes
    }


def dentro(api, consulta, creados):
    visibles = set(ids(recorrer(api, consulta)[0]))
    return {t for t, evento in creados.items() if evento.pk in visibles}


def test_day_limits_in_local_time(api_admin):
    # México (UTC-6, sin horario de verano en 2026): el día 2026-10-08 va de 06:00Z a 06:00Z.
    creados = sembrar_en(
        "2026-10-08T05:59:59.999999+00:00",  # 2026-10-07 23:59:59.999999 local
        "2026-10-08T06:00:00+00:00",  # 2026-10-08 00:00:00 local (inicio, incluido)
        "2026-10-08T18:00:00+00:00",
        "2026-10-09T05:59:59.999999+00:00",  # 2026-10-08 23:59:59.999999 local (fin, incluido)
        "2026-10-09T06:00:00+00:00",  # 2026-10-09 00:00:00 local (fuera)
    )

    assert dentro(api_admin, "desde=2026-10-08&hasta=2026-10-08", creados) == {
        "2026-10-08T06:00:00+00:00",
        "2026-10-08T18:00:00+00:00",
        "2026-10-09T05:59:59.999999+00:00",
    }
    assert "2026-10-09T06:00:00+00:00" not in dentro(api_admin, "hasta=2026-10-08", creados)
    assert "2026-10-08T05:59:59.999999+00:00" not in dentro(api_admin, "desde=2026-10-08", creados)
    assert "2026-10-09T06:00:00+00:00" in dentro(api_admin, "desde=2026-10-08", creados)
    assert len(dentro(api_admin, "hasta=2026-10-09", creados)) == 5  # hasta incluye todo el día


def test_the_upper_bound_includes_the_whole_last_day(api_admin):
    creados = sembrar_en("2026-10-08T12:00:00+00:00", "2026-10-09T05:00:00+00:00")

    assert len(dentro(api_admin, "hasta=2026-10-08", creados)) == 2  # 23:00 local del día 8


def test_a_25_hour_day_when_clocks_go_back(api_admin):
    # 2022-10-30: termina el horario de verano de México (02:00 CDT -> 01:00 CST): el día mide 25 h.
    creados = sembrar_en(
        "2022-10-30T04:59:59+00:00",  # 2022-10-29 23:59:59 CDT (UTC-5): día anterior
        "2022-10-30T05:00:00+00:00",  # 2022-10-30 00:00:00 CDT: inicio
        "2022-10-30T06:30:00+00:00",  # 01:30 hora local por segunda vez (ya CST)
        "2022-10-31T05:59:59+00:00",  # 2022-10-30 23:59:59 CST (UTC-6): último segundo del día
        "2022-10-31T06:00:00+00:00",  # 2022-10-31 00:00:00 CST: día siguiente
    )

    assert dentro(api_admin, "desde=2022-10-30&hasta=2022-10-30", creados) == {
        "2022-10-30T05:00:00+00:00",
        "2022-10-30T06:30:00+00:00",
        "2022-10-31T05:59:59+00:00",
    }


def test_a_23_hour_day_when_clocks_go_forward(api_admin):
    # 2022-04-03: empieza el horario de verano (02:00 CST -> 03:00 CDT): el día mide 23 h.
    creados = sembrar_en(
        "2022-04-03T05:59:59+00:00",  # 2022-04-02 23:59:59 CST: día anterior
        "2022-04-03T06:00:00+00:00",  # 2022-04-03 00:00:00 CST: inicio
        "2022-04-03T09:00:00+00:00",  # 04:00 CDT, ya después del salto
        "2022-04-04T04:59:59+00:00",  # 2022-04-03 23:59:59 CDT (UTC-5): último segundo
        "2022-04-04T05:00:00+00:00",  # 2022-04-04 00:00:00 CDT: día siguiente
    )

    assert dentro(api_admin, "desde=2022-04-03&hasta=2022-04-03", creados) == {
        "2022-04-03T06:00:00+00:00",
        "2022-04-03T09:00:00+00:00",
        "2022-04-04T04:59:59+00:00",
    }


def test_local_time_in_the_response_follows_the_dst_offset(api_admin):
    sembrar_en(
        "2022-07-01T12:00:00+00:00", "2022-12-01T12:00:00+00:00", "2026-07-01T12:00:00+00:00"
    )

    locales = {e["ocurrido_en_local"] for e in recorrer(api_admin)[0]}

    assert locales == {
        "2022-07-01T07:00:00-05:00",  # verano 2022: CDT
        "2022-12-01T06:00:00-06:00",  # invierno: CST
        "2026-07-01T06:00:00-06:00",  # 2026 ya no tiene horario de verano
    }


def test_the_date_filters_follow_the_configured_time_zone(api_admin, settings):
    settings.TIME_ZONE = "Asia/Tokyo"  # UTC+9: el día 2026-10-08 local empieza el 07 a las 15:00Z
    from django.utils import timezone

    timezone.activate("Asia/Tokyo")
    try:
        creados = sembrar_en("2026-10-07T14:59:59+00:00", "2026-10-07T15:00:00+00:00")

        assert dentro(api_admin, "desde=2026-10-08&hasta=2026-10-08", creados) == {
            "2026-10-07T15:00:00+00:00"
        }
    finally:
        timezone.deactivate()


# --- sin consultas N+1 y sin datos que no deben salir ---


def numero_de_consultas(api, consulta):
    with CaptureQueriesContext(connection) as consultas:
        assert pedir(api, consulta).status_code == 200
    return list(consultas)


def test_no_n_plus_one_the_user_comes_in_the_same_query(api_admin):
    usuarios = [UsuarioFactory(correo=f"u{i}@empresa.test") for i in range(30)]
    for usuario in usuarios:
        sembrar(1, usuario=usuario, inicio=INICIO + timedelta(minutes=usuario.pk))

    pocas = numero_de_consultas(api_admin, "tamano=3")
    muchas = numero_de_consultas(api_admin, "tamano=30")

    reales = [q for q in muchas if "SAVEPOINT" not in q["sql"]]  # ruido de la transacción de prueba
    assert len(pocas) == len(muchas)  # no crece con el número de eventos ni de usuarios
    assert len(reales) <= 5  # sesión + usuario autenticado + eventos (+ guardado de sesión)
    consultas_de_eventos = [q["sql"] for q in muchas if 'FROM "audit_evento"' in q["sql"]]
    assert len(consultas_de_eventos) == 1
    assert 'JOIN "accounts_usuario"' in consultas_de_eventos[0]  # select_related


def test_the_events_query_never_selects_password_hashes(api_admin, admin):
    sembrar(5, usuario=admin)

    consulta = next(
        q["sql"] for q in numero_de_consultas(api_admin, "") if 'FROM "audit_evento"' in q["sql"]
    )

    assert "password" not in consulta.lower()
    assert "intentos_fallidos" not in consulta


def test_antes_and_despues_never_show_keys_outside_the_whitelist(api_admin):
    # Defensa en profundidad: una fila insertada saltándose registrar_evento no filtra nada.
    Evento.objects.create(
        tipo="USUARIO_EDITADO",
        descripcion="fila con claves ajenas",
        antes={"nombre": "A", "password": "x", "intentos_fallidos": 4},
        despues={"correo": "a@e.test", "token": "t", "hash": "argon2$x"},
    )

    evento = recorrer(api_admin)[0][0]

    assert evento["antes"] == {"nombre": "A"}
    assert evento["despues"] == {"correo": "a@e.test"}


@pytest.fixture
def real_hasher(settings):
    settings.PASSWORD_HASHERS = base.PASSWORD_HASHERS
    cuentas.dummy_hash.cache_clear()
    yield
    cuentas.dummy_hash.cache_clear()


def test_real_flows_never_leak_hashes_passwords_or_foreign_keys(real_hasher, admin):
    """Con Argon2 real: crea, restablece, cambia, bloquea y falla; luego lee TODA la bitácora."""
    clave_1, clave_2, clave_3 = (
        "tornillo-azul-77-nube",
        "nube-de-algodon-88-azul",
        "cafe-con-leche-99",
    )
    api = Api()
    assert api.login(admin.correo).status_code == 200
    api.post(
        "/api/v1/usuarios/",
        {"nombre": "Luis", "correo": "luis@empresa.test", "rol": "TECNICO", "contrasena": clave_1},
    )
    otro = UsuarioFactory(correo="otro@empresa.test")
    api.post(f"/api/v1/usuarios/{otro.pk}/restablecer-contrasena/", {"contrasena": clave_2})
    api.call("PATCH", f"/api/v1/usuarios/{otro.pk}/", {"nombre": "Otro Nombre"})
    api.post(f"/api/v1/usuarios/{otro.pk}/cambiar-rol/", {"rol": "ESPECTADOR"})
    api.post(f"/api/v1/usuarios/{otro.pk}/desactivar/")
    api.post(f"/api/v1/usuarios/{otro.pk}/reactivar/")
    api.post(CAMBIAR, {"contrasena_actual": DEFAULT_PASSWORD, "contrasena_nueva": clave_3})
    api.post(CAMBIAR, {"contrasena_actual": "equivocada-77", "contrasena_nueva": clave_3})
    for _ in range(5):  # fallos y bloqueo
        Api().login("otro@empresa.test", "mal-intento-9")
    Api().login("nadie@empresa.test", "mal-intento-9")
    api = Api()
    api.login(admin.correo, clave_3)

    eventos, _ = recorrer(api, "tamano=100")

    assert len(eventos) >= 14
    texto = json.dumps(eventos)
    for secreto in (
        "argon2$",
        "pbkdf2",
        "md5$",
        clave_1,
        clave_2,
        clave_3,
        "equivocada-77",
        "mal-intento-9",
        DEFAULT_PASSWORD,
        "nadie@empresa.test",  # el correo de un intento contra un usuario inexistente
    ):
        assert secreto not in texto, secreto
    for evento in eventos:
        for lado in ("antes", "despues"):
            assert set(evento[lado] or {}) <= CAMPOS_PERMITIDOS, evento
    tipos = {e["tipo"] for e in eventos}
    assert {"USUARIO_CREADO", "USUARIO_CONTRASENA_RESTABLECIDA", "CUENTA_BLOQUEADA"} <= tipos
    assert {"USUARIO_CONTRASENA_CAMBIADA", "SESION_FALLIDA", "SESION_INICIADA"} <= tipos
