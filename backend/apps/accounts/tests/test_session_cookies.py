import json
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.sessions.models import Session

from apps.core.testing import CAMBIAR, CSRF, DEFAULT_PASSWORD, LOGIN, LOGOUT, ME, Api

pytestmark = pytest.mark.django_db

BACKEND_DIR = Path(__file__).resolve().parents[3]

PROD_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    "DJANGO_SECRET_KEY": "k" * 64,
    "DJANGO_ALLOWED_HOSTS": "metrologia.example.com",
    "DB_NAME": "x",
    "DB_USER": "app",
    "DB_PASSWORD": "una-clave-real",
    "TRUSTED_PROXY_CIDRS": "172.28.0.0/24",
}
COOKIE_SETTINGS = [
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_SECURE",
    "SESSION_COOKIE_HTTPONLY",
    "SESSION_COOKIE_SAMESITE",
    "SESSION_COOKIE_PATH",
    "SESSION_COOKIE_DOMAIN",
    "CSRF_COOKIE_NAME",
    "CSRF_COOKIE_SECURE",
    "CSRF_COOKIE_HTTPONLY",
    "CSRF_COOKIE_SAMESITE",
    "CSRF_COOKIE_PATH",
    "CSRF_COOKIE_DOMAIN",
    "SESSION_COOKIE_AGE",
    "SESSION_SAVE_EVERY_REQUEST",
]


def prod_settings() -> dict:
    code = (
        "from django.conf import settings; import json; "
        f"print(json.dumps({{n: getattr(settings, n) for n in {COOKIE_SETTINGS!r}}}))"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=PROD_ENV,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_session_cookie_attributes_in_dev(api, usuario):
    cookie = api.login(usuario.correo).cookies["sessionid"]

    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Lax"
    assert cookie["max-age"] == 30 * 60
    assert cookie["path"] == "/"
    assert not cookie["domain"]
    assert not cookie["secure"]  # solo HTTPS en producción


def test_csrf_cookie_attributes_in_dev(api):
    cookie = api.client.get(CSRF).cookies["csrftoken"]

    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Lax"


def test_prod_cookies_are_secure_host_prefixed_and_host_compatible():
    prod = prod_settings()

    assert prod["SESSION_COOKIE_NAME"] == "__Host-sessionid"
    assert prod["CSRF_COOKIE_NAME"] == "__Host-csrftoken"
    assert prod["SESSION_COOKIE_SECURE"] is True
    assert prod["CSRF_COOKIE_SECURE"] is True
    assert prod["SESSION_COOKIE_HTTPONLY"] is True
    assert prod["CSRF_COOKIE_HTTPONLY"] is True
    assert prod["SESSION_COOKIE_SAMESITE"] == prod["CSRF_COOKIE_SAMESITE"] == "Lax"
    # Requisitos del prefijo __Host-: Path=/ y sin Domain.
    assert prod["SESSION_COOKIE_PATH"] == prod["CSRF_COOKIE_PATH"] == "/"
    assert prod["SESSION_COOKIE_DOMAIN"] is None
    assert prod["CSRF_COOKIE_DOMAIN"] is None
    assert prod["SESSION_COOKIE_AGE"] == 1800
    assert prod["SESSION_SAVE_EVERY_REQUEST"] is True


# --- CSRF obligatorio -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ruta", "cuerpo"),
    [
        (LOGIN, {"correo": "ana@empresa.test", "contrasena": DEFAULT_PASSWORD}),
        (LOGOUT, {}),
        (
            CAMBIAR,
            {"contrasena_actual": DEFAULT_PASSWORD, "contrasena_nueva": "otra-clave-larga-77"},
        ),
    ],
)
def test_csrf_is_required_for_unsafe_endpoints(api, usuario, ruta, cuerpo):
    anonimo = api.post(ruta, cuerpo, csrf=False)
    assert anonimo.status_code == 403
    assert anonimo.json()["codigo"] == "csrf_invalido"
    assert "CSRF" in anonimo.json()["detalle"]

    api.login(usuario.correo)
    autenticado = api.post(ruta, cuerpo, csrf=False)
    assert autenticado.status_code == 403
    assert autenticado.json()["codigo"] == "csrf_invalido"
    assert "CSRF" in autenticado.json()["detalle"]


def test_a_wrong_csrf_token_is_rejected(api):
    api.csrf()
    api.token = "x" * 64

    assert api.post(LOGIN, {"correo": "a@e.test", "contrasena": "x"}).status_code == 403


def test_csrf_rejection_does_not_touch_the_failure_counter(api, usuario):
    api.post(LOGIN, {"correo": usuario.correo, "contrasena": "mal"}, csrf=False)

    usuario.refresh_from_db()
    assert usuario.intentos_fallidos == 0


def test_cors_is_closed(api):
    preflight = api.client.options(
        LOGIN,
        HTTP_ORIGIN="https://evil.example",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    cruzada = api.client.get(CSRF, HTTP_ORIGIN="https://evil.example")

    for respuesta in (preflight, cruzada):
        assert not any(h.lower().startswith("access-control-") for h in respuesta.headers)
    assert "corsheaders" not in str(settings.INSTALLED_APPS) + str(settings.MIDDLEWARE)


def test_csrf_trusted_origins_come_from_the_environment():
    env = {**PROD_ENV, "CSRF_TRUSTED_ORIGINS": "https://a.example.com,https://b.example.com"}
    code = "from django.conf import settings; print(settings.CSRF_TRUSTED_ORIGINS)"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stdout.strip() == "['https://a.example.com', 'https://b.example.com']"
    assert settings.CSRF_TRUSTED_ORIGINS == []  # sin variable: ningún origen extra


# --- rotación, invalidación e inactividad -------------------------------------------------


def test_session_key_is_rotated_on_login(api, usuario):
    previa = api.client.session  # crea una sesión anónima con cookie
    previa["preexistente"] = "fijada por un atacante"
    previa.save()
    antes = api.session_cookie

    api.login(usuario.correo)

    despues = api.session_cookie
    assert antes
    assert despues
    assert antes != despues
    assert not Session.objects.filter(session_key=antes).exists()  # la vieja ya no sirve


def test_idle_session_expires_after_30_minutes_and_activity_extends_it(api, clock, usuario):
    api.login(usuario.correo)

    clock.advance(minutes=29)
    assert api.get(ME).status_code == 200  # la actividad renueva la ventana
    clock.advance(minutes=29)
    assert api.get(ME).status_code == 200  # 58 min en total, pero 29 de inactividad
    clock.advance(minutes=31)

    assert api.get(ME).status_code == 401


def test_session_idle_time_is_configurable(api, clock, usuario, settings):
    settings.SESSION_COOKIE_AGE = 60
    api.login(usuario.correo)

    clock.advance(seconds=61)

    assert api.get(ME).status_code == 401


def test_anonymous_visits_do_not_create_sessions(api):
    api.csrf()
    api.get(ME)
    Api().login("nadie@empresa.test", "mal")

    assert Session.objects.count() == 0
