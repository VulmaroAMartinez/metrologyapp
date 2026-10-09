"""Cabeceras de seguridad y cookies contra el stack real.

- A través de Caddy (API de desarrollo, HTTP): nosniff, Referrer-Policy, frame-ancestors y
  Cache-Control: no-store en respuestas de autenticación, de datos de usuarios y de bitácora,
  incluidos los errores (401, 403, 404, 405).
- Contra la API en modo producción (gunicorn real, con X-Forwarded-Proto: https declarado por la
  red de confianza): HSTS, X-Frame-Options y cookies Secure con prefijo __Host-.
"""

import os
import uuid

import pytest
from cliente import Cliente, cookies_por_nombre

ADMIN_CORREO = os.environ["PROXYTEST_ADMIN_CORREO"]
ADMIN_CLAVE = os.environ["PROXYTEST_ADMIN_CONTRASENA"]
CLAVE = "tornillo-azul-77-nube"


def verificar_cabeceras(cabeceras, *, hsts, contexto=""):
    assert cabeceras["x-content-type-options"] == "nosniff", contexto
    assert cabeceras["referrer-policy"] == "same-origin", contexto
    assert "frame-ancestors 'none'" in cabeceras["content-security-policy"] or hsts, contexto
    assert cabeceras["cache-control"] == "no-store", contexto
    assert "server" not in cabeceras, contexto  # no revela el software
    if hsts:
        assert cabeceras["strict-transport-security"] == "max-age=3600"
    else:
        assert "strict-transport-security" not in cabeceras, contexto  # HSTS solo sobre HTTPS


@pytest.fixture(scope="module")
def tecnico(admin):
    correo = f"{uuid.uuid4().hex[:10]}@empresa.test"
    estado, _, creado = admin.pedir(
        "POST",
        "/api/v1/usuarios/",
        {"nombre": "Técnico Cabeceras", "correo": correo, "rol": "TECNICO", "contrasena": CLAVE},
    )
    assert estado == 201
    cliente = Cliente()
    assert cliente.iniciar_sesion(correo, CLAVE)[0] == 200
    return cliente, creado


# --- a través de Caddy ---


def test_security_headers_on_every_kind_of_response_through_caddy(admin, tecnico):
    cliente_tecnico, creado = tecnico
    anonimo = Cliente()
    anonimo.obtener_csrf()
    _, _, yo = admin.pedir("GET", "/api/v1/auth/me/")
    correo = f"{uuid.uuid4().hex[:10]}@empresa.test"
    respuestas = {
        # autenticación
        "csrf 200": anonimo.pedir("GET", "/api/v1/auth/csrf/"),
        "login 401": anonimo.pedir(
            "POST",
            "/api/v1/auth/login/",
            {"correo": "nadie@empresa.test", "contrasena": "x-mala-1"},
        ),
        "login 400": anonimo.pedir("POST", "/api/v1/auth/login/", {}),
        "login 405": anonimo.pedir("GET", "/api/v1/auth/login/"),
        "me 401": Cliente().pedir("GET", "/api/v1/auth/me/"),
        "me 200": admin.pedir("GET", "/api/v1/auth/me/"),
        "cambiar-contrasena 403 (técnico)": cliente_tecnico.pedir(
            "POST",
            "/api/v1/auth/cambiar-contrasena/",
            {"contrasena_actual": CLAVE, "contrasena_nueva": "x"},
        ),
        # datos de usuarios
        "usuarios 401": Cliente().pedir("GET", "/api/v1/usuarios/"),
        "usuarios 403 (técnico)": cliente_tecnico.pedir("GET", "/api/v1/usuarios/"),
        "usuarios lista 200": admin.pedir("GET", "/api/v1/usuarios/?tamano=5"),
        "usuario detalle 200": admin.pedir("GET", f"/api/v1/usuarios/{yo['id']}/"),
        "usuario detalle 404": admin.pedir("GET", "/api/v1/usuarios/987654321/"),
        "usuario crear 201": admin.pedir(
            "POST",
            "/api/v1/usuarios/",
            {"nombre": "Nueva", "correo": correo, "rol": "ESPECTADOR", "contrasena": CLAVE},
        ),
        "usuario crear 400": admin.pedir("POST", "/api/v1/usuarios/", {"nombre": "X"}),
        "usuario editar 200": admin.pedir(
            "PATCH", f"/api/v1/usuarios/{creado['id']}/", {"nombre": "Editado"}
        ),
        "usuario borrar 405": admin.pedir("DELETE", f"/api/v1/usuarios/{creado['id']}/", {}),
        # bitácora
        "bitacora 200": admin.pedir("GET", "/api/v1/bitacora/eventos/?tamano=3"),
        "bitacora 400": admin.pedir("GET", "/api/v1/bitacora/eventos/?desde=hoy"),
        "bitacora 403 (técnico)": cliente_tecnico.pedir("GET", "/api/v1/bitacora/eventos/"),
        "bitacora 405": admin.pedir("POST", "/api/v1/bitacora/eventos/", {}),
        # fuera de DRF
        "ruta inexistente 404": admin.pedir("GET", "/api/v1/no-existe/"),
    }

    for nombre, (estado, cabeceras, _) in respuestas.items():
        esperado = int(nombre.split()[1] if nombre.split()[1].isdigit() else nombre.split()[2])
        assert estado == esperado, (nombre, estado)
        verificar_cabeceras(cabeceras, hsts=False, contexto=nombre)


def test_the_redirect_the_production_api_issues_over_http_also_carries_caddys_headers():
    estado, cabeceras, _ = Cliente().pedir("GET", "/prod-api/api/v1/auth/csrf/")

    assert estado == 301
    assert cabeceras["location"].startswith("https://")
    assert cabeceras["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in cabeceras["content-security-policy"]
    assert "strict-transport-security" not in cabeceras  # un redireccionamiento HTTP no lleva HSTS


def test_cookies_through_caddy_in_development_have_the_documented_attributes(admin):
    cliente = Cliente()
    cliente.obtener_csrf()

    estado, _, _ = cliente.pedir(
        "POST", "/api/v1/auth/login/", {"correo": "nadie@empresa.test", "contrasena": "x-mala-1"}
    )
    assert estado == 401
    assert cliente.set_cookie == []

    estado, _, _ = cliente.pedir(
        "POST", "/api/v1/auth/login/", {"correo": ADMIN_CORREO, "contrasena": ADMIN_CLAVE}
    )
    cookies = cookies_por_nombre(cliente.set_cookie)
    assert estado == 200
    assert set(cookies) == {"sessionid", "csrftoken"}
    sesion = cookies["sessionid"]
    assert sesion["httponly"] is True
    assert sesion["samesite"] == "Lax"
    assert sesion["path"] == "/"
    assert sesion["max-age"] == "1800"
    assert "domain" not in sesion
    assert "secure" not in sesion
    assert cookies["csrftoken"]["httponly"] is True
    assert cookies["csrftoken"]["samesite"] == "Lax"

    cliente.obtener_csrf()  # el token rota al iniciar sesión
    assert cliente.pedir("POST", "/api/v1/auth/logout/", {})[0] == 204
    borrada = cookies_por_nombre(cliente.set_cookie)["sessionid"]
    assert borrada["valor"] == "" or borrada.get("max-age") == "0"  # la cookie se elimina


# --- contra la API en modo producción (gunicorn real) ---


@pytest.fixture
def produccion():
    """La API con settings.prod, alcanzada directamente desde la red interna de confianza con
    X-Forwarded-Proto: https (como lo haría Caddy con TLS)."""
    cliente = Cliente(
        host="api-prod",
        puerto=8000,
        cabeceras_fijas={
            "Host": "localhost",
            "X-Forwarded-Proto": "https",
            "Origin": "https://localhost",
        },
    )
    cliente.obtener_csrf()
    return cliente


def test_production_security_headers_include_hsts_and_frame_denial(produccion):
    for metodo, ruta, cuerpo in [
        ("GET", "/api/v1/auth/csrf/", None),
        ("GET", "/api/v1/health/", None),
        ("GET", "/api/v1/auth/me/", None),  # 401
        ("POST", "/api/v1/auth/login/", {"correo": "nadie@empresa.test", "contrasena": "x-mala-1"}),
    ]:
        estado, cabeceras, _ = produccion.pedir(metodo, ruta, cuerpo)

        assert estado in {200, 401}, (ruta, estado)
        assert cabeceras["strict-transport-security"] == "max-age=3600"
        assert cabeceras["x-frame-options"] == "DENY", ruta
        assert cabeceras["x-content-type-options"] == "nosniff", ruta
        assert cabeceras["referrer-policy"] == "same-origin", ruta
        assert cabeceras["cross-origin-opener-policy"] == "same-origin", ruta
        if ruta.startswith("/api/v1/auth/"):
            assert cabeceras["cache-control"] == "no-store", ruta


def test_production_cookies_are_secure_host_prefixed_and_a_failed_login_sets_none(produccion):
    produccion.pedir("GET", "/api/v1/auth/csrf/")
    csrf = cookies_por_nombre(produccion.set_cookie)["__Host-csrftoken"]
    assert csrf["secure"] is True
    assert csrf["httponly"] is True
    assert csrf["samesite"] == "Lax"
    assert csrf["path"] == "/"
    assert "domain" not in csrf

    estado, _, _ = produccion.pedir(
        "POST", "/api/v1/auth/login/", {"correo": "nadie@empresa.test", "contrasena": "x-mala-1"}
    )
    assert estado == 401
    assert produccion.set_cookie == []

    estado, _, _ = produccion.pedir(
        "POST", "/api/v1/auth/login/", {"correo": ADMIN_CORREO, "contrasena": ADMIN_CLAVE}
    )
    cookies = cookies_por_nombre(produccion.set_cookie)
    assert estado == 200
    assert {"__Host-sessionid", "__Host-csrftoken"} <= set(cookies)
    sesion = cookies["__Host-sessionid"]
    assert sesion["secure"] is True
    assert sesion["httponly"] is True
    assert sesion["samesite"] == "Lax"
    assert sesion["path"] == "/"
    assert "domain" not in sesion
    assert sesion["max-age"] == "1800"
    assert "sessionid" not in cookies  # la cookie sin prefijo no existe en producción
