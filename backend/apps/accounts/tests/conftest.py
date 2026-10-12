"""Fixtures de las pruebas de accounts. Las comunes a varias apps están en el `conftest.py`
raíz; los ayudantes generales, en `apps/core/testing.py`."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Usuario
from apps.core.testing import Api, UsuarioFactory


@pytest.fixture
def usuario(db):
    return UsuarioFactory(correo="ana@empresa.test", nombre="Ana Pérez", rol=Usuario.Rol.ADMIN)


@pytest.fixture
def ana(db):
    return UsuarioFactory(correo="ana@empresa.test", nombre="Ana", rol=Usuario.Rol.ADMIN)


@pytest.fixture
def beto(db):
    return UsuarioFactory(correo="beto@empresa.test", nombre="Beto", rol=Usuario.Rol.ADMIN)


@pytest.fixture
def carla(db):
    return UsuarioFactory(correo="carla@empresa.test", nombre="Carla", rol=Usuario.Rol.TECNICO)


@pytest.fixture
def tecnico(db):
    return UsuarioFactory(correo="tecnico@empresa.test", nombre="Tec Nico")


@pytest.fixture
def api_ana(usuario, relaxed_rate):
    """Cliente con la sesión abierta (login real) de `usuario`, el administrador Ana Pérez."""
    api = Api()
    assert api.login(usuario.correo).status_code == 200
    return api


@pytest.fixture
def cuentas(db):
    """Un caso por cada razón por la que un login puede fallar (ver `ayudantes.CASOS`)."""
    UsuarioFactory(correo="activo@empresa.test")
    UsuarioFactory(correo="inactivo@empresa.test", activo=False)
    UsuarioFactory(
        correo="bloqueado@empresa.test", bloqueado_hasta=timezone.now() + timedelta(minutes=10)
    )
    sin_clave = UsuarioFactory(correo="sinclave@empresa.test")
    sin_clave.set_unusable_password()
    sin_clave.save()
