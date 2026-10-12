"""Eventos de autenticación: misma transacción que el cambio, sin datos sensibles y sin romper
la indistinguibilidad del login."""

import json
import logging

import pytest
from django.contrib.sessions.models import Session
from django.db import OperationalError, connection
from django.test.utils import CaptureQueriesContext
from psycopg.errors import LockNotAvailable

from apps.accounts.models import Usuario
from apps.accounts.services import autenticacion
from apps.audit.models import Evento
from apps.core.testing import CAMBIAR, DEFAULT_PASSWORD, LOGIN, Api, UsuarioFactory

from .ayudantes import CASOS, evento_que_falla, lanza

pytestmark = pytest.mark.django_db

CORREO_AJENO = "secreto.persona@dominio-raro.test"


def tipos():
    return list(Evento.objects.order_by("id").values_list("tipo", flat=True))


def volcado():
    """Todas las columnas de todas las filas de eventos, como un solo texto."""
    return json.dumps(list(Evento.objects.values()), default=str, ensure_ascii=False)


def inserciones_de_eventos(consultas):
    return [q for q in consultas if q["sql"].startswith('INSERT INTO "audit_evento"')]


# --- SESION_FALLIDA: una sola inserción, sin el correo ---------------------------------------


def test_unknown_email_writes_one_event_without_user_and_never_stores_the_email(api):
    api.login(CORREO_AJENO, "mal-intento-1")

    evento = Evento.objects.get()
    assert evento.tipo == "SESION_FALLIDA"
    assert evento.usuario is None
    assert evento.ip == "127.0.0.1"
    assert evento.despues == {"motivo": "no_existe"}
    texto = volcado()
    assert CORREO_AJENO not in texto
    assert "secreto.persona" not in texto
    assert "dominio-raro" not in texto


def test_every_failure_case_does_exactly_one_event_insert(cuentas, relaxed_rate):
    for nombre, (correo, contrasena) in CASOS.items():
        api = Api()
        api.csrf()
        antes = Evento.objects.count()
        with CaptureQueriesContext(connection) as consultas:
            respuesta = api.post(LOGIN, {"correo": correo, "contrasena": contrasena})

        assert respuesta.status_code == 401, nombre
        assert len(inserciones_de_eventos(consultas)) == 1, nombre
        assert Evento.objects.count() == antes + 1, nombre
        assert Evento.objects.latest("id").tipo == "SESION_FALLIDA", nombre


def test_failure_responses_stay_identical_with_events_enabled(cuentas, relaxed_rate):
    respuestas = [Api().login(correo, contrasena) for correo, contrasena in CASOS.values()]

    referencia = respuestas[0]
    for respuesta in respuestas:
        assert respuesta.status_code == 401
        assert respuesta.content == referencia.content
        assert dict(respuesta.headers) == dict(referencia.headers)


def test_known_user_failure_is_attributed_to_the_user_with_the_reason(api, usuario):
    api.login(usuario.correo, "mal-intento-1")

    evento = Evento.objects.get()
    assert (evento.tipo, evento.usuario, evento.despues) == (
        "SESION_FALLIDA",
        usuario,
        {"motivo": "contrasena_incorrecta"},
    )
    assert "ana@empresa.test" not in volcado()


@pytest.mark.django_db(transaction=True)
def test_counter_and_event_are_committed_even_though_the_response_is_401():
    usuario = UsuarioFactory(correo="ana@empresa.test")
    api = Api()

    respuesta = api.login(usuario.correo, "mal-intento-1")

    assert respuesta.status_code == 401
    usuario.refresh_from_db()
    assert usuario.intentos_fallidos == 1  # no se revirtió
    assert tipos() == ["SESION_FALLIDA"]


def test_contended_attempt_writes_a_single_failure_event(api, usuario):
    error = OperationalError("could not obtain lock")
    error.__cause__ = LockNotAvailable()
    with (
        autenticacion.sustituir(obtener_usuario_bloqueado=lanza(error)),
        CaptureQueriesContext(connection) as consultas,
    ):
        respuesta = api.login(usuario.correo, DEFAULT_PASSWORD)

    assert respuesta.status_code == 401
    assert len(inserciones_de_eventos(consultas)) == 1
    evento = Evento.objects.get()
    assert (evento.tipo, evento.usuario, evento.despues) == (
        "SESION_FALLIDA",
        usuario,
        {"motivo": "intento_simultaneo"},
    )


# --- CUENTA_BLOQUEADA ---------------------------------------------------------------------------


def test_the_failure_that_locks_the_account_also_records_the_lock(
    api, clock, relaxed_rate, usuario
):
    for _ in range(4):
        api.login(usuario.correo, "mal-intento-1")
    assert tipos() == ["SESION_FALLIDA"] * 4

    with CaptureQueriesContext(connection) as consultas:
        api.login(usuario.correo, "mal-intento-1")  # el 5.º

    assert len(inserciones_de_eventos(consultas)) == 2
    assert tipos()[4:] == ["SESION_FALLIDA", "CUENTA_BLOQUEADA"]
    bloqueo = Evento.objects.get(tipo="CUENTA_BLOQUEADA")
    assert bloqueo.usuario == usuario
    assert bloqueo.despues == {"bloqueado_hasta": "2026-10-08T12:15:30+00:00"}


def test_attempts_during_the_lock_do_not_repeat_the_lock_event(api, clock, relaxed_rate, usuario):
    for _ in range(5):
        api.login(usuario.correo, "mal-intento-1")

    api.login(usuario.correo, DEFAULT_PASSWORD)  # bloqueada: ni la correcta entra

    assert tipos().count("CUENTA_BLOQUEADA") == 1
    assert Evento.objects.latest("id").despues == {"motivo": "bloqueado"}


# --- SESION_INICIADA ------------------------------------------------------------------------


def test_successful_login_writes_one_event_with_user_and_ip(api, usuario):
    api.login(usuario.correo)

    evento = Evento.objects.get()
    assert (evento.tipo, evento.usuario, evento.ip) == ("SESION_INICIADA", usuario, "127.0.0.1")
    assert evento.antes is None
    assert evento.despues is None


def test_if_the_login_event_fails_the_login_is_rolled_back(usuario):
    api = Api(raise_request_exception=False)
    with evento_que_falla():
        respuesta = api.login(usuario.correo)

    usuario.refresh_from_db()
    assert respuesta.status_code == 500
    assert Session.objects.count() == 0  # la sesión se revirtió con el evento
    assert usuario.last_login is None
    assert Evento.objects.count() == 0


def test_if_the_failure_event_fails_the_counter_is_rolled_back(usuario):
    api = Api(raise_request_exception=False)
    with evento_que_falla():
        respuesta = api.login(usuario.correo, "mal-intento-1")

    usuario.refresh_from_db()
    assert respuesta.status_code == 500
    assert usuario.intentos_fallidos == 0
    assert Evento.objects.count() == 0


# --- 429 no genera evento -------------------------------------------------------------------


def test_rate_limited_requests_write_no_event_only_a_log_line(
    api, clock, usuario, caplog, settings
):
    settings.LOGIN_MAX_FAILED_ATTEMPTS = 1000  # aquí se prueba el 429, no el bloqueo
    caplog.set_level(logging.INFO)
    for _ in range(5):
        api.login(usuario.correo, "mal-intento-1")
    assert Evento.objects.count() == 5

    respuesta = api.login(usuario.correo, "mal-intento-1")

    assert respuesta.status_code == 429
    assert Evento.objects.count() == 5  # el 429 no escribió nada
    registros = [r.getMessage() for r in caplog.records if "Límite de tasa" in r.getMessage()]
    assert len(registros) == 1
    assert usuario.correo not in registros[0]
    assert "mal-intento" not in registros[0]


# --- USUARIO_CONTRASENA_CAMBIADA ------------------------------------------------------------


@pytest.fixture
def administrador(db):
    return UsuarioFactory(correo="nuevo@empresa.test", rol=Usuario.Rol.ADMIN)


def cambiar(api, actual=DEFAULT_PASSWORD, nueva="tornillo-azul-77-nube"):
    return api.post(CAMBIAR, {"contrasena_actual": actual, "contrasena_nueva": nueva})


def test_password_change_writes_an_event_without_password_data(api, administrador):
    api.login(administrador.correo)
    Evento.objects.all()  # el SESION_INICIADA ya existe

    assert cambiar(api).status_code == 200

    evento = Evento.objects.get(tipo="USUARIO_CONTRASENA_CAMBIADA")
    assert evento.usuario == administrador
    assert (evento.antes, evento.despues) == (None, None)  # nunca datos de la contraseña
    assert evento.ip == "127.0.0.1"


def test_rejected_password_changes_write_no_event(api, administrador):
    api.login(administrador.correo)

    cambiar(api, actual="no-es-la-actual-1")
    cambiar(api, nueva=DEFAULT_PASSWORD)
    cambiar(api, nueva="qwertyuiop")

    assert "USUARIO_CONTRASENA_CAMBIADA" not in tipos()


def test_if_the_password_event_fails_the_change_is_rolled_back(administrador):
    api = Api(raise_request_exception=False)
    api.login(administrador.correo)
    with evento_que_falla():
        respuesta = cambiar(api)

    administrador.refresh_from_db()
    assert respuesta.status_code == 500
    assert administrador.check_password(DEFAULT_PASSWORD)  # la contraseña no cambió


def test_logout_writes_no_event(api, usuario):
    api.login(usuario.correo)
    antes = Evento.objects.count()

    api.post("/api/v1/auth/logout/")

    assert Evento.objects.count() == antes


# --- nada sensible en ninguna columna ------------------------------------------------------


def test_no_password_hash_or_secret_appears_in_any_event_column(api, administrador):
    api.login("nadie@empresa.test", "contrasena-ajena-9")
    api.login(administrador.correo, "contrasena-mala-8")
    api.login(administrador.correo)
    cambiar(api, nueva="clave-nueva-larga-55")
    administrador.refresh_from_db()

    texto = volcado()
    for secreto in (
        "contrasena-ajena-9",
        "contrasena-mala-8",
        DEFAULT_PASSWORD,
        "clave-nueva-larga-55",
        administrador.password,
        "argon2",
        "pbkdf2",
        "md5$",
        api.session_cookie,
    ):
        assert secreto not in texto, secreto
    assert Usuario.objects.get(pk=administrador.pk).intentos_fallidos == 0
