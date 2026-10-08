"""Flujo completo de gestión de usuarios a través de Caddy (stack real).

El administrador lo crea `deploy/proxytest.sh` con `crear_admin` (contraseña aleatoria por
variable de entorno), de modo que nace con el cambio de contraseña obligatorio. El flujo:
el administrador cambia su contraseña, crea un usuario, este inicia sesión con la temporal,
es bloqueado por el cambio pendiente, cambia su contraseña y ya puede entrar.
"""

import http.client
import json
import os
import uuid
from urllib.parse import urlparse

import pytest

BASE = urlparse(os.environ["PROXY_BASE_URL"])
ADMIN_CORREO = os.environ["PROXYTEST_ADMIN_CORREO"]
ADMIN_CLAVE = os.environ["PROXYTEST_ADMIN_CONTRASENA"]
CLAVE_NUEVA = "tornillo-azul-77-nube"


class Cliente:
    """Navegador mínimo sobre http.client: guarda cookies y envía el token CSRF."""

    def __init__(self):
        self.cookies, self.token = {}, None

    def pedir(self, metodo, ruta, cuerpo=None):
        cabeceras = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        if metodo != "GET":
            if self.token is None:
                self.obtener_csrf()
            cabeceras["X-CSRFToken"] = self.token
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo)
            cabeceras["Content-Type"] = "application/json"
        conexion = http.client.HTTPConnection(BASE.hostname, BASE.port, timeout=15)
        conexion.request(metodo, ruta, body=datos, headers=cabeceras)
        respuesta = conexion.getresponse()
        crudo = respuesta.read()
        for cookie in respuesta.msg.get_all("Set-Cookie") or []:
            nombre, _, valor = cookie.split(";")[0].partition("=")
            if valor:
                self.cookies[nombre] = valor
            else:
                self.cookies.pop(nombre, None)
        conexion.close()
        cuerpo_json = json.loads(crudo) if crudo else None
        return respuesta.status, {k.lower(): v for k, v in respuesta.getheaders()}, cuerpo_json

    def obtener_csrf(self):
        _, _, cuerpo = self.pedir("GET", "/api/v1/auth/csrf/")
        self.token = cuerpo["csrfToken"]

    def iniciar_sesion(self, correo, contrasena):
        self.obtener_csrf()
        resultado = self.pedir(
            "POST", "/api/v1/auth/login/", {"correo": correo, "contrasena": contrasena}
        )
        if resultado[0] == 200:
            self.obtener_csrf()  # el token rota al iniciar sesión
        return resultado


@pytest.fixture(scope="module")
def admin():
    """El administrador del arranque entra con su clave inicial y debe cambiarla."""
    cliente = Cliente()
    estado, _, perfil = cliente.iniciar_sesion(ADMIN_CORREO, ADMIN_CLAVE)
    assert estado == 200 and perfil["debe_cambiar_contrasena"] is True and perfil["rol"] == "ADMIN"
    estado, _, cuerpo = cliente.pedir("GET", "/api/v1/usuarios/")
    assert (estado, cuerpo["codigo"]) == (403, "debe_cambiar_contrasena")
    estado, _, perfil = cliente.pedir(
        "POST",
        "/api/v1/auth/cambiar-contrasena/",
        {"contrasena_actual": ADMIN_CLAVE, "contrasena_nueva": CLAVE_NUEVA + "-admin"},
    )
    assert estado == 200 and perfil["debe_cambiar_contrasena"] is False
    return cliente


def test_full_user_management_flow_through_caddy(admin):
    # 1. Crea un usuario: recibe la contraseña temporal una sola vez, sin caché.
    correo = f"{uuid.uuid4().hex[:10]}@empresa.test"
    estado, cabeceras, creado = admin.pedir(
        "POST", "/api/v1/usuarios/", {"nombre": "Nuevo Técnico", "correo": correo, "rol": "TECNICO"}
    )
    assert estado == 201
    assert cabeceras["cache-control"] == "no-store"
    temporal = creado["contrasena_temporal"]
    assert len(temporal) >= 16 and creado["debe_cambiar_contrasena"] is True
    assert "intentos_fallidos" not in creado and "password" not in creado

    # La temporal no se puede volver a consultar.
    estado, _, lista = admin.pedir("GET", f"/api/v1/usuarios/?buscar={correo}")
    assert estado == 200 and lista["count"] == 1
    assert temporal not in json.dumps(lista) and "contrasena_temporal" not in json.dumps(lista)

    # 2. El usuario inicia sesión con la temporal: solo puede ver su perfil y cambiar la clave.
    nuevo = Cliente()
    estado, _, perfil = nuevo.iniciar_sesion(correo, temporal)
    assert estado == 200 and perfil["debe_cambiar_contrasena"] is True
    estado, _, cuerpo = nuevo.pedir("GET", "/api/v1/usuarios/")
    assert (estado, cuerpo["codigo"]) == (403, "debe_cambiar_contrasena")
    assert nuevo.pedir("GET", "/api/v1/auth/me/")[0] == 200

    # 3. Cambia la contraseña; ya no lo bloquea el cambio y, como TECNICO, no administra.
    estado, _, perfil = nuevo.pedir(
        "POST",
        "/api/v1/auth/cambiar-contrasena/",
        {"contrasena_actual": temporal, "contrasena_nueva": CLAVE_NUEVA},
    )
    assert estado == 200 and perfil["debe_cambiar_contrasena"] is False
    estado, _, cuerpo = nuevo.pedir("GET", "/api/v1/usuarios/")
    assert (estado, cuerpo["codigo"]) == (403, "permiso_denegado")

    # 4. La contraseña nueva funciona y la temporal murió.
    assert Cliente().iniciar_sesion(correo, CLAVE_NUEVA)[0] == 200
    assert Cliente().iniciar_sesion(correo, temporal)[0] == 401


def test_a_reset_closes_the_session_and_forces_a_new_change_through_caddy(admin):
    correo = f"{uuid.uuid4().hex[:10]}@empresa.test"
    _, _, creado = admin.pedir(
        "POST", "/api/v1/usuarios/", {"nombre": "Otro", "correo": correo, "rol": "ESPECTADOR"}
    )
    usuario = Cliente()
    assert usuario.iniciar_sesion(correo, creado["contrasena_temporal"])[0] == 200

    estado, cabeceras, reseteo = admin.pedir(
        "POST", f"/api/v1/usuarios/{creado['id']}/restablecer-contrasena/", {}
    )

    assert estado == 200 and cabeceras["cache-control"] == "no-store"
    assert usuario.pedir("GET", "/api/v1/auth/me/")[0] == 401  # su sesión dejó de valer
    estado, _, perfil = Cliente().iniciar_sesion(correo, reseteo["contrasena_temporal"])
    assert estado == 200 and perfil["debe_cambiar_contrasena"] is True
