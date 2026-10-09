import os

import pytest
from cliente import Cliente

ADMIN_CORREO = os.environ["PROXYTEST_ADMIN_CORREO"]
ADMIN_CLAVE = os.environ["PROXYTEST_ADMIN_CONTRASENA"]


@pytest.fixture(scope="module")
def admin():
    """El administrador A (creado por deploy/proxytest.sh con crear_admin) entra directo: no hay
    cambio de contraseña forzado."""
    cliente = Cliente()
    estado, _, perfil = cliente.iniciar_sesion(ADMIN_CORREO, ADMIN_CLAVE)
    assert estado == 200
    assert perfil["rol"] == "ADMIN"
    assert set(perfil) == {"id", "nombre", "correo", "rol", "numero_nomina"}
    return cliente
