"""Cabeceras y cookies bajo config.settings.prod.

Corre Django en un intérprete aparte con los settings de producción (la validación de
producción ocurre al importar el módulo) contra una base real, con la petición declarada como
HTTPS por un proxy de confianza. `make test-proxy` repite lo esencial contra el servidor real.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
CLAVE = "tornillo-azul-77-nube"

GUION = """
import json
import os

import django
from django.conf import settings

# La base de pruebas usa la contraseña de EJEMPLO del rol `app`, que settings.prod rechaza (con
# razón). Para que el guion arranque, DB_PASSWORD lleva un valor válido para esa validación y la
# contraseña real se inyecta aquí, antes de abrir ninguna conexión.
settings.DATABASES["default"]["PASSWORD"] = os.environ["CONTRASENA_REAL_DE_BD"]
django.setup()
from django.test import Client

from apps.accounts.models import Usuario

HOST = "metrologia.example.com"
Usuario.objects.create_user("admin@empresa.test", "Admin", "ADMIN", password="%(clave)s")


def cliente(seguro=True):
    extra = {"HTTP_X_FORWARDED_PROTO": "https", "HTTP_ORIGIN": "https://" + HOST} if seguro else {}
    return Client(enforce_csrf_checks=True, HTTP_HOST=HOST, REMOTE_ADDR="172.28.0.5", **extra)


def resumen(r):
    cookies = {}
    for nombre, m in r.cookies.items():
        cookies[nombre] = {
            "secure": bool(m["secure"]),
            "httponly": bool(m["httponly"]),
            "samesite": m["samesite"],
            "path": m["path"],
            "domain": m["domain"],
            "max_age": m["max-age"],
        }
    return {
        "estado": r.status_code,
        "cabeceras": {k.lower(): v for k, v in r.headers.items()},
        "cookies": cookies,
        "location": r.headers.get("Location"),
    }


def post(c, ruta, cuerpo, token):
    return c.post(ruta, cuerpo, content_type="application/json", HTTP_X_CSRFTOKEN=token)


c = cliente()
r = c.get("/api/v1/auth/csrf/")
token = json.loads(r.content)["csrfToken"]
salida = {
    "csrf": resumen(r),
    "health": resumen(c.get("/api/v1/health/")),
    "me_401": resumen(c.get("/api/v1/auth/me/")),
}
mala = {"correo": "nadie@empresa.test", "contrasena": "x-mala-1"}
buena = {"correo": "admin@empresa.test", "contrasena": "%(clave)s"}
salida["login_401"] = resumen(post(c, "/api/v1/auth/login/", mala, token))
salida["login_200"] = resumen(post(c, "/api/v1/auth/login/", buena, token))
salida["usuarios"] = resumen(c.get("/api/v1/usuarios/"))
salida["bitacora"] = resumen(c.get("/api/v1/bitacora/eventos/"))
http = cliente(seguro=False)
salida["http_csrf"] = resumen(http.get("/api/v1/auth/csrf/"))
salida["http_health"] = resumen(http.get("/api/v1/health/"))
print("RESULTADO=" + json.dumps(salida))
"""


@pytest.fixture(scope="module")
def prod(roles_db):
    env = {
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": "k" * 64,
        "DJANGO_ALLOWED_HOSTS": "metrologia.example.com",
        "DB_HOST": os.environ.get("DB_HOST", "db"),
        "DB_NAME": roles_db,
        "DB_USER": "app",  # el rol de ejecución, no el dueño
        "DB_PASSWORD": "una-clave-valida-solo-para-la-validacion",
        "CONTRASENA_REAL_DE_BD": os.environ["APP_DB_PASSWORD"],
        "TRUSTED_PROXY_CIDRS": "172.28.0.0/24",
    }
    resultado = subprocess.run(  # noqa: S603
        [sys.executable, "-c", GUION % {"clave": CLAVE}],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    linea = next(x for x in resultado.stdout.splitlines() if x.startswith("RESULTADO="))
    return json.loads(linea.removeprefix("RESULTADO="))


HTTPS = ["csrf", "health", "me_401", "login_401", "login_200", "usuarios", "bitacora"]


@pytest.mark.parametrize("nombre", HTTPS)
def test_https_responses_carry_the_production_security_headers(prod, nombre):
    cabeceras = prod[nombre]["cabeceras"]

    assert cabeceras["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert cabeceras["x-frame-options"] == "DENY"
    assert cabeceras["x-content-type-options"] == "nosniff"
    assert cabeceras["referrer-policy"] == "same-origin"
    assert cabeceras["cross-origin-opener-policy"] == "same-origin"
    assert "server" not in cabeceras


@pytest.mark.parametrize(
    "nombre", ["csrf", "health", "me_401", "login_401", "login_200", "usuarios", "bitacora"]
)
def test_every_api_response_is_no_store(prod, nombre):
    assert prod[nombre]["cabeceras"]["cache-control"] == "no-store"


def test_statuses_are_the_expected_ones(prod):
    esperado = {
        "csrf": 200,
        "health": 200,
        "me_401": 401,
        "login_401": 401,
        "login_200": 200,
        "usuarios": 200,
        "bitacora": 200,
    }

    assert {k: prod[k]["estado"] for k in esperado} == esperado


def test_hsts_is_only_sent_over_https_and_http_is_redirected(prod):
    redireccion = prod["http_csrf"]
    assert redireccion["estado"] == 301
    assert redireccion["location"].startswith("https://")
    assert "strict-transport-security" not in redireccion["cabeceras"]
    # El healthcheck interno va por HTTP y está exento de la redirección, pero sin HSTS.
    assert prod["http_health"]["estado"] == 200
    assert "strict-transport-security" not in prod["http_health"]["cabeceras"]


def test_production_cookies_are_secure_httponly_samesite_and_host_prefixed(prod):
    csrf = prod["csrf"]["cookies"]
    sesion = prod["login_200"]["cookies"]

    assert set(csrf) == {"__Host-csrftoken"}
    assert {"__Host-sessionid", "__Host-csrftoken"} <= set(sesion)
    for cookie in [*csrf.values(), *sesion.values()]:
        assert cookie["secure"]
        assert cookie["httponly"]
        assert cookie["samesite"] == "Lax"
        assert cookie["path"] == "/"
        assert cookie["domain"] == ""
    assert sesion["__Host-sessionid"]["max_age"] == 1800


def test_a_failed_login_and_an_unauthenticated_request_set_no_cookies(prod):
    assert prod["login_401"]["cookies"] == {}
    assert prod["me_401"]["cookies"] == {}
    assert "set-cookie" not in prod["login_401"]["cabeceras"]
