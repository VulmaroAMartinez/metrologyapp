"""Gestión de usuarios y contraseñas a través de Caddy (stack real).

El primer administrador (A) lo crea `deploy/proxytest.sh` con `crear_admin`. Solo un
administrador asigna contraseñas: A crea un técnico con la contraseña que escribe, el técnico
entra pero no puede cambiar la suya, y A restablece la del otro administrador (B), que entra
con la nueva. No hay contraseñas temporales ni cambio forzado al entrar.
"""

import json
import os
import uuid

from cliente import Cliente

ADMIN_CORREO = os.environ["PROXYTEST_ADMIN_CORREO"]
CLAVE_TECNICO = "tornillo-azul-77-nube"
CLAVE_B = "nube-de-algodon-88-azul"
CLAVE_NUEVA = "cafe-con-leche-99-dulce"


def crear_usuario(admin, rol, contrasena):
    correo = f"{uuid.uuid4().hex[:10]}@empresa.test"
    estado, cabeceras, creado = admin.pedir(
        "POST",
        "/api/v1/usuarios/",
        {"nombre": "Persona Prueba", "correo": correo, "rol": rol, "contrasena": contrasena},
    )
    assert estado == 201, creado
    assert cabeceras["cache-control"] == "no-store"
    return correo, creado


def test_the_admin_creates_a_technician_who_cannot_change_their_password(admin):
    correo, creado = crear_usuario(admin, "TECNICO", CLAVE_TECNICO)
    assert CLAVE_TECNICO not in json.dumps(creado)
    assert "contrasena" not in creado
    assert "contrasena_temporal" not in creado
    assert "debe_cambiar_contrasena" not in creado

    tecnico = Cliente()
    estado, _, perfil = tecnico.iniciar_sesion(correo, CLAVE_TECNICO)  # entra directo
    assert estado == 200
    assert perfil["rol"] == "TECNICO"
    assert tecnico.pedir("GET", "/api/v1/auth/me/")[0] == 200

    # No puede cambiar su contraseña por ninguna vía de la API: 403 permiso_denegado.
    estado, _, cuerpo = tecnico.pedir(
        "POST",
        "/api/v1/auth/cambiar-contrasena/",
        {"contrasena_actual": CLAVE_TECNICO, "contrasena_nueva": CLAVE_NUEVA},
    )
    assert (estado, cuerpo["codigo"]) == (403, "permiso_denegado")
    assert Cliente().iniciar_sesion(correo, CLAVE_TECNICO)[0] == 200  # la contraseña no cambió
    estado, _, cuerpo = tecnico.pedir("GET", "/api/v1/usuarios/")
    assert (estado, cuerpo["codigo"]) == (403, "permiso_denegado")  # tampoco administra


def test_admin_a_resets_admin_b_and_b_logs_in_with_the_new_password(admin):
    correo_b, creado_b = crear_usuario(admin, "ADMIN", CLAVE_B)
    admin_b = Cliente()
    assert admin_b.iniciar_sesion(correo_b, CLAVE_B)[0] == 200
    assert admin_b.pedir("GET", "/api/v1/usuarios/")[0] == 200  # B también administra

    # A restablece la contraseña de B: solo manda la contraseña nueva, sin reautenticación.
    estado, cabeceras, cuerpo = admin.pedir(
        "POST",
        f"/api/v1/usuarios/{creado_b['id']}/restablecer-contrasena/",
        {"contrasena": CLAVE_NUEVA},
    )

    assert estado == 200
    assert cabeceras["cache-control"] == "no-store"
    assert CLAVE_NUEVA not in json.dumps(cuerpo)
    assert "contrasena" not in cuerpo
    assert admin_b.pedir("GET", "/api/v1/auth/me/")[0] == 401  # su sesión se cerró
    assert Cliente().iniciar_sesion(correo_b, CLAVE_B)[0] == 401  # la anterior ya no sirve
    nueva_sesion = Cliente()
    estado, _, perfil = nueva_sesion.iniciar_sesion(correo_b, CLAVE_NUEVA)
    assert estado == 200
    assert perfil["rol"] == "ADMIN"
    assert nueva_sesion.pedir("GET", "/api/v1/usuarios/")[0] == 200


def test_an_admin_cannot_reset_their_own_password_through_administration(admin):
    _, _, perfil = admin.pedir("GET", "/api/v1/auth/me/")

    estado, _, cuerpo = admin.pedir(
        "POST",
        f"/api/v1/usuarios/{perfil['id']}/restablecer-contrasena/",
        {"contrasena": CLAVE_NUEVA},
    )

    assert (estado, cuerpo["codigo"]) == (403, "auto_modificacion_no_permitida")
    assert admin.pedir("GET", "/api/v1/auth/me/")[0] == 200  # su sesión sigue
