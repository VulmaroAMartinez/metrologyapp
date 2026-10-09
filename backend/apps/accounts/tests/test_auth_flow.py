import pytest
from django.contrib.sessions.models import Session
from django.utils import timezone

from .conftest import CSRF, LOGIN, LOGOUT, ME, Api
from .factories import DEFAULT_PASSWORD, UsuarioFactory

pytestmark = pytest.mark.django_db

CAMPOS_PUBLICOS = {"id", "nombre", "correo", "rol", "numero_nomina"}


def test_csrf_endpoint_returns_token_and_sets_cookie(api):
    respuesta = api.client.get(CSRF)

    assert respuesta.status_code == 200
    assert len(respuesta.json()["csrfToken"]) >= 32
    assert "csrftoken" in respuesta.cookies
    assert respuesta["Cache-Control"] == "no-store"


def test_login_returns_the_public_profile_only(api, usuario):
    respuesta = api.login(usuario.correo)

    assert respuesta.status_code == 200
    assert set(respuesta.json()) == CAMPOS_PUBLICOS
    assert respuesta.json() == {
        "id": usuario.pk,
        "nombre": "Ana Pérez",
        "correo": "ana@empresa.test",
        "rol": "ADMIN",
        "numero_nomina": None,
    }
    assert respuesta["Cache-Control"] == "no-store"


def test_login_updates_last_login(api, usuario):
    assert usuario.last_login is None

    api.login(usuario.correo)

    usuario.refresh_from_db()
    assert usuario.last_login is not None
    assert usuario.last_login <= timezone.now()


def test_me_never_exposes_hash_or_lock_counters(api, usuario):
    api.login(usuario.correo)

    respuesta = api.get(ME)

    assert respuesta.status_code == 200
    assert set(respuesta.json()) == CAMPOS_PUBLICOS
    cuerpo = respuesta.content.decode()
    for prohibido in (usuario.password, "password", "intentos", "bloqueado", "last_login"):
        assert prohibido not in cuerpo
    assert respuesta["Cache-Control"] == "no-store"


def test_me_without_session_is_401_with_authenticate_header(api):
    respuesta = api.get(ME)

    assert respuesta.status_code == 401
    assert respuesta["WWW-Authenticate"] == "Session"


def test_logout_returns_204_and_invalidates_the_session_on_the_server(api, usuario):
    api.login(usuario.correo)
    clave = api.session_cookie
    assert Session.objects.filter(session_key=clave).exists()

    assert api.post(LOGOUT).status_code == 204

    assert not Session.objects.filter(session_key=clave).exists()
    assert api.get(ME).status_code == 401


def test_a_stolen_session_cookie_is_useless_after_logout(api, usuario):
    api.login(usuario.correo)
    robada = api.session_cookie
    api.post(LOGOUT)

    otro = Api()
    otro.client.cookies["sessionid"] = robada

    assert otro.get(ME).status_code == 401


def test_logout_without_session_is_idempotent(api):
    assert api.post(LOGOUT).status_code == 204


def test_deactivated_user_loses_the_session(api, usuario):
    api.login(usuario.correo)
    usuario.activo = False
    usuario.save()

    assert api.get(ME).status_code == 401


def test_login_endpoint_rejects_get(api):
    assert api.client.get(LOGIN).status_code == 405


def test_sessions_of_different_users_do_not_mix(api):
    ana = UsuarioFactory(correo="ana@empresa.test", nombre="Ana")
    UsuarioFactory(correo="luis@empresa.test", nombre="Luis")
    api.login(ana.correo, DEFAULT_PASSWORD)

    assert api.get(ME).json()["nombre"] == "Ana"
