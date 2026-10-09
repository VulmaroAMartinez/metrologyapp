"""El borrado de sesiones es limpieza posterior al commit (`transaction.on_commit`, robust=True).

La invalidación real NO depende de él: un usuario desactivado falla `is_active` en cada petición y,
tras restablecer o cambiar la contraseña, el hash de sesión ya no coincide. Estas pruebas usan
transacciones reales (`transaction=True`) para que los callbacks de on_commit se ejecuten de verdad.
"""

import logging
from unittest import mock

import pytest
from django.contrib.sessions.models import Session

from apps.accounts import services
from apps.audit.models import Evento

from .conftest import CAMBIAR, ME, Api
from .factories import DEFAULT_PASSWORD, UsuarioFactory

pytestmark = pytest.mark.django_db(transaction=True)

BASE = "/api/v1/usuarios/"
CLAVE_2 = "nube-de-algodon-88-azul"


def ruta(pk, accion):
    return f"{BASE}{pk}/{accion}/"


def abrir(usuario):
    api = Api()
    assert api.login(usuario.correo).status_code == 200
    return api


@pytest.fixture
def admin_api(usuario, relaxed_rate):
    return abrir(usuario)


@pytest.fixture
def tecnico(db):
    return UsuarioFactory(correo="tecnico@empresa.test", nombre="Tec Nico")


def sin_limpieza():
    return mock.patch.object(services, "invalidar_sesiones", return_value=0)


def limpieza_que_falla():
    return mock.patch.object(
        services, "invalidar_sesiones", side_effect=RuntimeError("detalle-interno-xyz")
    )


@pytest.mark.parametrize("limpieza", [sin_limpieza, limpieza_que_falla], ids=["apagada", "falla"])
def test_a_deactivated_users_old_session_gets_401_even_without_the_cleanup(
    limpieza, admin_api, tecnico, relaxed_rate
):
    sesion = abrir(tecnico)
    assert sesion.get(ME).status_code == 200

    with limpieza():
        respuesta = admin_api.post(ruta(tecnico.pk, "desactivar"))

    assert respuesta.status_code == 200  # un fallo de la limpieza no tumba la operación
    siguiente = sesion.get(ME)
    assert siguiente.status_code == 401
    assert siguiente.json()["codigo"] == "no_autenticado"
    # La fila sigue ahí (la limpieza no corrió): el 401 viene de is_active, no del borrado.
    assert Session.objects.filter(session_key=sesion.session_cookie).exists()


@pytest.mark.parametrize("limpieza", [sin_limpieza, limpieza_que_falla], ids=["apagada", "falla"])
def test_a_reset_users_old_session_gets_401_even_without_the_cleanup(
    limpieza, admin_api, tecnico, relaxed_rate
):
    sesion = abrir(tecnico)
    assert sesion.get(ME).status_code == 200

    with limpieza():
        respuesta = admin_api.post(
            ruta(tecnico.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2}
        )

    assert respuesta.status_code == 200
    assert sesion.get(ME).status_code == 401  # su hash de sesión ya no coincide
    assert Api().login(tecnico.correo, CLAVE_2).status_code == 200


def test_the_other_sessions_of_an_admin_who_changes_their_password_get_401(usuario, relaxed_rate):
    desde_la_que_cambia, otra = abrir(usuario), abrir(usuario)
    assert otra.get(ME).status_code == 200

    with sin_limpieza():
        cambio = desde_la_que_cambia.post(
            CAMBIAR,
            {"contrasena_actual": DEFAULT_PASSWORD, "contrasena_nueva": "tornillo-azul-77-nube"},
        )

    assert cambio.status_code == 200
    assert otra.get(ME).status_code == 401
    assert desde_la_que_cambia.get(ME).status_code == 200  # la actual se conserva


def test_a_failing_cleanup_is_logged_without_sensitive_data(admin_api, tecnico, caplog):
    sesion = abrir(tecnico)
    caplog.set_level(logging.DEBUG)

    with limpieza_que_falla():
        respuesta = admin_api.post(
            ruta(tecnico.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2}
        )

    assert respuesta.status_code == 200
    registros = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(registros) == 1
    mensaje = registros[0].getMessage()
    assert f"usuario_id={tecnico.pk}" in mensaje
    assert "RuntimeError" in mensaje
    texto_completo = caplog.text
    for sensible in (
        tecnico.correo,
        CLAVE_2,
        DEFAULT_PASSWORD,
        sesion.session_cookie,
        "detalle-interno-xyz",
    ):
        assert sensible not in texto_completo, sensible


def test_the_real_on_commit_callback_deletes_the_targets_session_rows(admin_api, tecnico):
    uno, dos = abrir(tecnico), abrir(tecnico)
    ajena = abrir(UsuarioFactory(correo="otra@empresa.test"))
    assert Session.objects.count() == 4  # la del administrador, dos del técnico y una ajena

    admin_api.post(ruta(tecnico.pk, "desactivar"))

    quedan = set(Session.objects.values_list("session_key", flat=True))
    assert uno.session_cookie not in quedan
    assert dos.session_cookie not in quedan
    assert ajena.session_cookie in quedan  # solo las del usuario afectado


OPERACIONES = {
    "desactivar": lambda admin, objetivo: services.desactivar_usuario(admin.pk, objetivo.pk),
    "restablecer": lambda admin, objetivo: services.restablecer_contrasena(
        admin.pk, objetivo.pk, CLAVE_2
    ),
}


@pytest.mark.parametrize("operacion", OPERACIONES)
def test_a_failed_operation_closes_no_session(operacion, usuario, tecnico, relaxed_rate):
    sesion = abrir(tecnico)
    antes = Evento.objects.count()

    with (
        mock.patch.object(services, "registrar_evento", side_effect=RuntimeError("falla")),
        pytest.raises(RuntimeError),
    ):
        OPERACIONES[operacion](usuario, tecnico)

    assert Evento.objects.count() == antes
    assert Session.objects.filter(session_key=sesion.session_cookie).exists()
    assert sesion.get(ME).status_code == 200  # nada cambió: la sesión sigue valiendo
