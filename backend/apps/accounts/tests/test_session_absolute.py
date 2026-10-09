"""Vida máxima absoluta de la sesión (12 horas, configurable con SESSION_ABSOLUTE_HOURS).

La inactividad (30 min) renueva la ventana deslizante, pero nunca más allá del límite absoluto
contado desde el inicio de sesión. Expirar no genera evento (CLAUDE.md: no se registran
cierres ni expiraciones de sesión).
"""

import pytest
from django.contrib.sessions.models import Session

from apps.audit.models import Evento

from .conftest import CAMBIAR, ME
from .factories import DEFAULT_PASSWORD

pytestmark = pytest.mark.django_db

DE_AUTENTICACION = {"_auth_user_id", "_auth_user_backend", "_auth_user_hash"}


def test_the_default_absolute_lifetime_is_12_hours(settings):
    assert settings.SESSION_ABSOLUTE_HOURS == 12


def test_activity_cannot_keep_a_session_alive_past_12_hours(api, clock, usuario):
    assert api.login(usuario.correo).status_code == 200

    for _ in range(28):  # 28 x 25 min = 11 h 40 min, siempre dentro de la inactividad
        clock.advance(minutes=25)
        assert api.get(ME).status_code == 200
    clock.advance(minutes=25)  # 12 h 05 min desde el inicio de sesión

    assert api.get(ME).status_code == 401


def test_the_limit_is_exact_and_configurable(api, clock, usuario, settings):
    settings.SESSION_ABSOLUTE_HOURS = 1
    api.login(usuario.correo)

    for minutos in (29, 29):
        clock.advance(minutes=minutos)
        assert api.get(ME).status_code == 200  # 58 min
    clock.advance(minutes=1, seconds=59)
    assert api.get(ME).status_code == 200  # 59 min 59 s
    clock.advance(seconds=1)

    assert api.get(ME).status_code == 401  # 60 min 00 s: el límite ya se cumplió


def test_an_expired_session_is_deleted_and_the_cookie_is_useless(api, clock, usuario, settings):
    settings.SESSION_ABSOLUTE_HOURS = 1
    api.login(usuario.correo)
    cookie = api.session_cookie
    for _ in range(3):
        clock.advance(minutes=21)
        api.get(ME)

    assert api.get(ME).status_code == 401
    assert not Session.objects.filter(session_key=cookie).exists()
    assert Session.objects.count() == 0


def test_a_new_login_starts_a_new_window(api, clock, usuario, settings):
    settings.SESSION_ABSOLUTE_HOURS = 1
    api.login(usuario.correo)
    for _ in range(2):
        clock.advance(minutes=29)
        assert api.get(ME).status_code == 200

    assert api.login(usuario.correo).status_code == 200
    clock.advance(minutes=29)  # 87 min desde el primer inicio, 29 desde el segundo

    assert api.get(ME).status_code == 200


def test_changing_the_password_does_not_extend_the_window(api, clock, usuario, settings):
    settings.SESSION_ABSOLUTE_HOURS = 1
    settings.LOGIN_RATE_PER_IP_EMAIL = settings.LOGIN_RATE_PER_IP = 1000
    api.login(usuario.correo)
    clock.advance(minutes=29)
    respuesta = api.post(
        CAMBIAR,
        {"contrasena_actual": DEFAULT_PASSWORD, "contrasena_nueva": "tornillo-azul-77-nube"},
    )
    assert respuesta.status_code == 200
    clock.advance(minutes=29)
    assert api.get(ME).status_code == 200  # 58 min

    clock.advance(minutes=3)

    assert api.get(ME).status_code == 401  # 61 min desde el inicio de sesión original


def test_a_session_without_a_start_time_is_rejected(api, usuario):
    """Una sesión sin marca de inicio (anterior a este control) no se acepta: falla cerrado."""
    api.login(usuario.correo)
    sesion = api.client.session
    for clave in set(sesion.keys()) - DE_AUTENTICACION:
        del sesion[clave]
    sesion.save()

    assert api.get(ME).status_code == 401


def test_expiring_writes_no_event(api, clock, usuario, settings):
    settings.SESSION_ABSOLUTE_HOURS = 1
    api.login(usuario.correo)
    antes = Evento.objects.count()
    for _ in range(3):
        clock.advance(minutes=21)
        api.get(ME)

    api.get(ME)

    assert Evento.objects.count() == antes
