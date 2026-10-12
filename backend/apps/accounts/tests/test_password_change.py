"""cambiar-contrasena: solo ADMIN (acción CAMBIAR_PROPIA_CONTRASENA).

Solo el administrador asigna y restablece contraseñas: técnicos y espectadores no pueden cambiar
la suya por ninguna vía de la API. No existe el cambio forzado al entrar.
"""

import logging

import pytest

from apps.accounts.models import Usuario
from apps.audit.models import Evento
from apps.core.testing import CAMBIAR, DEFAULT_PASSWORD, ME, Api, UsuarioFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.accounts.tests.urls_probe")]

PROBE = "/api/v1/probe/consultar/"
SIN_PERMISOS = "/api/v1/probe/sin-permisos/"
NUEVA = "tornillo-azul-77-nube"


def cambiar(api, actual=DEFAULT_PASSWORD, nueva=NUEVA):
    return api.post(CAMBIAR, {"contrasena_actual": actual, "contrasena_nueva": nueva})


# --- solo ADMIN ---


@pytest.mark.parametrize("rol", [Usuario.Rol.TECNICO, Usuario.Rol.ESPECTADOR])
def test_technicians_and_spectators_cannot_change_their_password(rol, relaxed_rate):
    persona = UsuarioFactory(correo="persona@empresa.test", rol=rol)
    api = Api()
    assert api.login(persona.correo).status_code == 200

    respuesta = cambiar(api)

    assert respuesta.status_code == 403
    assert set(respuesta.json()) == {"codigo", "detalle"}
    assert respuesta.json()["codigo"] == "permiso_denegado"
    persona.refresh_from_db()
    assert persona.check_password(DEFAULT_PASSWORD)  # nada cambió
    assert not Evento.objects.filter(tipo="USUARIO_CONTRASENA_CAMBIADA").exists()


def test_the_change_endpoint_requires_a_session():
    api = Api()
    api.csrf()

    assert cambiar(api).status_code == 401


# --- el cambio ---


def test_an_admin_changes_their_own_password(api_ana, usuario):
    respuesta = cambiar(api_ana)

    assert respuesta.status_code == 200
    assert set(respuesta.json()) == {"id", "nombre", "correo", "rol", "numero_nomina"}
    usuario.refresh_from_db()
    assert usuario.check_password(NUEVA)
    assert not usuario.check_password(DEFAULT_PASSWORD)
    assert api_ana.get(ME).status_code == 200  # la sesión que cambió la clave sigue vigente


def test_new_password_works_and_the_old_one_does_not(api_ana, usuario):
    cambiar(api_ana)
    api_ana.post("/api/v1/auth/logout/")

    assert Api().login(usuario.correo, DEFAULT_PASSWORD).status_code == 401
    assert Api().login(usuario.correo, NUEVA).status_code == 200


def test_the_change_writes_an_event_without_any_password_data(api_ana, usuario):
    cambiar(api_ana)

    evento = Evento.objects.get(tipo="USUARIO_CONTRASENA_CAMBIADA")
    assert evento.usuario == usuario
    assert evento.ip == "127.0.0.1"
    assert (evento.antes, evento.despues) == (None, None)
    assert NUEVA not in evento.descripcion
    assert usuario.password not in evento.descripcion


def test_changing_the_password_invalidates_the_other_sessions(usuario, relaxed_rate):
    equipo, celular = Api(), Api()
    equipo.login(usuario.correo)
    celular.login(usuario.correo)
    assert celular.get(ME).status_code == 200

    assert cambiar(equipo).status_code == 200

    assert equipo.get(ME).status_code == 200  # la sesión que cambió la clave sigue
    assert celular.get(ME).status_code == 401  # las demás se cierran


# --- validación del cambio ---


def test_wrong_current_password_is_rejected(api_ana, usuario):
    respuesta = cambiar(api_ana, actual="no-es-la-actual-1")

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert list(respuesta.json()["campos"]) == ["contrasena_actual"]
    usuario.refresh_from_db()
    assert usuario.check_password(DEFAULT_PASSWORD)


def test_new_password_must_differ_from_the_current_one(api_ana):
    respuesta = cambiar(api_ana, nueva=DEFAULT_PASSWORD)

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert list(respuesta.json()["campos"]) == ["contrasena_nueva"]


@pytest.mark.parametrize(
    ("nueva", "fragmento"),
    [
        ("Ab1-cdef9", "10 caracteres"),
        ("qwertyuiop", "muy común"),
        ("48291746503", "numérica"),
        ("anaperez", "muy similar"),
    ],
)
def test_new_password_goes_through_the_validators(api_ana, usuario, nueva, fragmento):
    respuesta = cambiar(api_ana, nueva=nueva)

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert any(fragmento in mensaje for mensaje in respuesta.json()["campos"]["contrasena_nueva"])
    assert nueva not in respuesta.content.decode()  # el mensaje no repite la contraseña
    usuario.refresh_from_db()
    assert usuario.check_password(DEFAULT_PASSWORD)


def test_change_attempts_are_rate_limited(usuario, clock):
    api = Api()
    api.login(usuario.correo)

    estados = [cambiar(api, actual=f"mal-{n}-contrasena").status_code for n in range(6)]

    assert estados == [400] * 5 + [429]


@pytest.mark.parametrize("cuerpo", [{}, {"contrasena_actual": "x"}, {"contrasena_nueva": "x"}])
def test_malformed_change_is_400(api_ana, cuerpo):
    assert api_ana.post(CAMBIAR, cuerpo).status_code == 400


def test_no_password_reaches_the_logs_or_events_during_the_change(api_ana, usuario, caplog):
    caplog.set_level(logging.DEBUG)

    cambiar(api_ana, actual="actual-incorrecta-1", nueva="otra-nueva-clave-9")
    cambiar(api_ana, nueva="qwertyuiop")
    cambiar(api_ana)

    usuario.refresh_from_db()
    eventos = str(list(Evento.objects.values()))
    for secreto in (
        "actual-incorrecta-1",
        "otra-nueva-clave-9",
        "qwertyuiop",
        DEFAULT_PASSWORD,
        NUEVA,
        usuario.password,
    ):
        assert secreto not in caplog.text
        assert secreto not in eventos


# --- vistas sin permisos declarados ---


def test_protected_endpoints_require_a_session():
    api = Api()
    api.csrf()

    assert api.get(PROBE).status_code == 401


def test_a_view_without_declared_permissions_is_denied_even_with_a_valid_session(api_ana):
    respuesta = api_ana.get(SIN_PERMISOS)

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "permiso_denegado"


# --- el límite de cambiar-contrasena tiene su propio ajuste -----------------------------------


def test_the_change_rate_limit_defaults_to_5_per_minute(settings):
    assert settings.PASSWORD_CHANGE_RATE_PER_USER == 5


def test_the_change_rate_limit_has_its_own_setting(usuario, clock, settings):
    settings.LOGIN_RATE_PER_IP_EMAIL = settings.LOGIN_RATE_PER_IP = 1000
    settings.PASSWORD_CHANGE_RATE_PER_USER = 2
    api = Api()
    assert api.login(usuario.correo).status_code == 200

    estados = [cambiar(api, actual="no-es-la-actual-1").status_code for _ in range(3)]

    assert estados == [400, 400, 429]


def test_the_login_rate_limit_does_not_touch_password_changes(usuario, clock, settings):
    settings.LOGIN_RATE_PER_IP_EMAIL = settings.LOGIN_RATE_PER_IP = 1000
    api = Api()
    assert api.login(usuario.correo).status_code == 200
    settings.LOGIN_RATE_PER_IP_EMAIL = 1  # un ajuste de login muy bajo...

    estados = [cambiar(api, actual="no-es-la-actual-1").status_code for _ in range(4)]

    assert 429 not in estados  # ...no limita el cambio de contraseña
