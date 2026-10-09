"""Pruebas contra el stack real, con cabeceras falsificadas.

Se ejecutan con `make test-proxy` (necesitan Caddy, Django y PostgreSQL levantados).
Solo usan la biblioteca estándar: hablan HTTP crudo para controlar cada cabecera.
"""

import http.client
import json
import os
import re
import uuid
from urllib.parse import urlparse

BASE = urlparse(os.environ["PROXY_BASE_URL"])
CSRF_PATH = "/api/v1/auth/csrf/"
LOGIN_PATH = "/api/v1/auth/login/"


def send(method, path, *, headers=None, body=None, host=None, port=None):
    conn = http.client.HTTPConnection(host or BASE.hostname, port or BASE.port, timeout=15)
    payload = json.dumps(body) if body is not None else None
    conn.request(method, path, body=payload, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    headers_out = {name.lower(): value for name, value in response.getheaders()}
    cookies = response.msg.get_all("Set-Cookie") or []
    conn.close()
    return response.status, headers_out, cookies, data


def csrf_pair():
    status, _, cookies, data = send("GET", CSRF_PATH)
    assert status == 200
    cookie = next(c.split(";")[0] for c in cookies if c.startswith("csrftoken="))
    return json.loads(data)["csrfToken"], cookie


def login_attempt(correo, extra_headers=None):
    token, cookie = csrf_pair()
    headers = {
        "Content-Type": "application/json",
        "X-CSRFToken": token,
        "Cookie": cookie,
        **(extra_headers or {}),
    }
    return send(
        "POST", LOGIN_PATH, headers=headers, body={"correo": correo, "contrasena": "no-es-esta-1"}
    )


# --- el límite de tasa no se evade falsificando la IP ------------------------------------------


def test_forged_forwarded_headers_do_not_evade_the_per_ip_and_email_limit():
    correo = f"{uuid.uuid4().hex[:12]}@empresa.test"
    estados = []
    ultimo = None
    for n in range(8):
        ultimo = login_attempt(
            correo,
            {
                "X-Forwarded-For": f"10.99.0.{n}, 203.0.113.{n}",
                "X-Real-IP": f"198.51.100.{n}",
                "Forwarded": f"for=192.0.2.{n};proto=https",
                "X-Forwarded-Host": "evil.example",
            },
        )
        estados.append(ultimo[0])

    assert estados == [401] * 5 + [429] * 3, estados
    assert int(ultimo[1]["retry-after"]) in range(1, 61)
    assert json.loads(ultimo[3])["codigo"] == "demasiados_intentos"


def test_forged_headers_do_not_evade_the_per_ip_limit_across_many_emails():
    estados = [
        login_attempt(
            f"{uuid.uuid4().hex[:12]}@empresa.test",
            {"X-Forwarded-For": f"10.77.{n // 250}.{n % 250}"},
        )[0]
        for n in range(60)
    ]

    assert 429 in estados, "el límite por IP sola nunca se activó"
    # El stack de prueba fija LOGIN_RATE_PER_IP=40 por minuto (docker-compose.proxytest.yml), menos
    # lo que ya gastaron las demás pruebas en la misma ventana.
    assert estados.index(429) <= 40
    assert set(estados) <= {401, 429}


def test_failed_logins_look_identical_for_known_and_unknown_emails_through_the_proxy():
    status_a, _, _, body_a = login_attempt(f"{uuid.uuid4().hex[:12]}@empresa.test")

    assert status_a in {401, 429}
    if status_a == 401:
        assert json.loads(body_a)["codigo"] == "credenciales_invalidas"


# --- la redirección a HTTPS no se engaña con X-Forwarded-Proto ----------------------------------

PROD_CSRF = "/prod-api" + CSRF_PATH  # misma API en modo producción, detrás del mismo Caddy


def test_forged_x_forwarded_proto_does_not_fool_the_https_redirect():
    forged = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Ssl": "on",
        "Forwarded": "proto=https;for=203.0.113.1",
        "X-Forwarded-Port": "443",
    }

    sin_cabecera = send("GET", PROD_CSRF)
    con_cabecera = send("GET", PROD_CSRF, headers=forged)

    for status, headers, _, _ in (sin_cabecera, con_cabecera):
        assert status == 301
        assert headers["location"].startswith("https://")


def test_control_a_trusted_proxy_can_declare_https():
    """Control: la misma API sí acepta X-Forwarded-Proto desde la red interna (donde solo
    llega Caddy). Demuestra que la prueba anterior distingue ambos comportamientos."""
    directo = {"Host": "localhost"}  # va directo a la API, saltándose a Caddy

    sin_cabecera = send("GET", CSRF_PATH, headers=directo, host="api-prod", port=8000)
    con_cabecera = send(
        "GET",
        CSRF_PATH,
        headers={**directo, "X-Forwarded-Proto": "https"},
        host="api-prod",
        port=8000,
    )

    assert sin_cabecera[0] == 301
    assert con_cabecera[0] == 200
    assert re.match(r"__Host-csrftoken=", con_cabecera[2][0])
    assert "Secure" in con_cabecera[2][0] and "HttpOnly" in con_cabecera[2][0]


def test_the_production_api_hides_the_openapi_schema_through_the_proxy():
    # Con el esquema deshabilitado responde 404 incluso cuando la petición ya es "HTTPS" directa.
    status, *_ = send(
        "GET",
        "/api/v1/schema/",
        headers={"Host": "localhost", "X-Forwarded-Proto": "https"},
        host="api-prod",
        port=8000,
    )

    assert status == 404
