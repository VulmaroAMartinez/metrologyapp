import logging

import pytest

from apps.accounts.models import Usuario

from .conftest import CAMBIAR, LOGOUT, ME, Api
from .factories import DEFAULT_PASSWORD, UsuarioFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.accounts.tests.urls_probe")]

PROBE = "/api/v1/probe/"
NUEVA = "tornillo-azul-77-nube"


@pytest.fixture
def forzado(db):
    return UsuarioFactory(
        correo="nuevo@empresa.test",
        nombre="Nuevo Usuario",
        rol=Usuario.Rol.TECNICO,
        debe_cambiar_contrasena=True,
    )


def cambiar(api, actual=DEFAULT_PASSWORD, nueva=NUEVA):
    return api.post(CAMBIAR, {"contrasena_actual": actual, "contrasena_nueva": nueva})


# --- cambio forzado --------------------------------------------------------------------------


def test_forced_change_blocks_every_endpoint_except_me_logout_and_change(api, forzado):
    respuesta = api.login(forzado.correo)
    assert respuesta.status_code == 200
    assert respuesta.json()["debe_cambiar_contrasena"] is True

    bloqueada = api.get(PROBE)
    assert bloqueada.status_code == 403
    assert bloqueada.json()["codigo"] == "debe_cambiar_contrasena"
    assert api.get(ME).status_code == 200
    assert api.get(ME).json()["debe_cambiar_contrasena"] is True
    assert cambiar(api, nueva="x").status_code == 400  # el endpoint responde (no 403)
    assert api.post(LOGOUT).status_code == 204


def test_user_without_pending_change_reaches_protected_endpoints(api, usuario):
    api.login(usuario.correo)

    assert api.get(PROBE).status_code == 200


def test_protected_endpoints_default_to_requiring_a_session(api):
    assert api.get(PROBE).status_code == 401


def test_changing_the_password_lifts_the_block(api, forzado):
    api.login(forzado.correo)

    respuesta = cambiar(api)

    assert respuesta.status_code == 200
    assert respuesta.json()["debe_cambiar_contrasena"] is False
    forzado.refresh_from_db()
    assert forzado.debe_cambiar_contrasena is False
    assert forzado.check_password(NUEVA)
    assert api.get(PROBE).status_code == 200  # la misma sesión sigue válida


def test_new_password_works_and_the_old_one_does_not(api, forzado):
    api.login(forzado.correo)
    cambiar(api)
    api.post(LOGOUT)

    assert Api().login(forzado.correo, DEFAULT_PASSWORD).status_code == 401
    assert Api().login(forzado.correo, NUEVA).status_code == 200


def test_changing_the_password_invalidates_the_other_sessions(forzado):
    equipo, celular = Api(), Api()
    equipo.login(forzado.correo)
    celular.login(forzado.correo)
    assert celular.get(ME).status_code == 200

    assert cambiar(equipo).status_code == 200

    assert equipo.get(ME).status_code == 200  # la sesión que cambió la clave sigue
    assert celular.get(ME).status_code == 401  # las demás se cierran


# --- validación del cambio -----------------------------------------------------------------


def test_wrong_current_password_is_rejected(api, forzado):
    api.login(forzado.correo)

    respuesta = cambiar(api, actual="no-es-la-actual-1")

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "contrasena_actual_incorrecta"
    forzado.refresh_from_db()
    assert forzado.debe_cambiar_contrasena is True


def test_new_password_must_differ_from_the_current_one(api, forzado):
    api.login(forzado.correo)

    respuesta = cambiar(api, nueva=DEFAULT_PASSWORD)

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "contrasena_repetida"


@pytest.mark.parametrize(
    ("nueva", "fragmento"),
    [
        ("Ab1-cdef9", "10 caracteres"),
        ("qwertyuiop", "muy común"),
        ("48291746503", "numérica"),
        ("nuevousuario", "muy similar"),
    ],
)
def test_new_password_goes_through_the_validators(api, forzado, nueva, fragmento):
    api.login(forzado.correo)

    respuesta = cambiar(api, nueva=nueva)

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "contrasena_invalida"
    assert any(fragmento in mensaje for mensaje in respuesta.json()["detalle"])
    forzado.refresh_from_db()
    assert forzado.debe_cambiar_contrasena is True
    assert forzado.check_password(DEFAULT_PASSWORD)


def test_change_requires_a_session(api):
    assert cambiar(api).status_code == 401


def test_change_attempts_are_rate_limited(api, clock, forzado):
    api.login(forzado.correo)

    estados = [cambiar(api, actual=f"mal-{n}-contrasena").status_code for n in range(6)]

    assert estados == [400] * 5 + [429]


@pytest.mark.parametrize("cuerpo", [{}, {"contrasena_actual": "x"}, {"contrasena_nueva": "x"}])
def test_malformed_change_is_400(api, forzado, cuerpo):
    api.login(forzado.correo)

    assert api.post(CAMBIAR, cuerpo).status_code == 400


def test_no_password_reaches_the_logs_during_the_change(api, forzado, caplog):
    caplog.set_level(logging.DEBUG)
    api.login(forzado.correo)

    cambiar(api, actual="actual-incorrecta-1", nueva="otra-nueva-clave-9")
    cambiar(api, nueva="qwertyuiop")
    cambiar(api)

    forzado.refresh_from_db()
    for secreto in (
        "actual-incorrecta-1",
        "otra-nueva-clave-9",
        "qwertyuiop",
        DEFAULT_PASSWORD,
        NUEVA,
        forzado.password,
    ):
        assert secreto not in caplog.text
