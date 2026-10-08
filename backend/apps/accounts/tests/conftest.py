from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Usuario

from .factories import DEFAULT_PASSWORD, UsuarioFactory

CSRF = "/api/v1/auth/csrf/"
LOGIN = "/api/v1/auth/login/"
LOGOUT = "/api/v1/auth/logout/"
ME = "/api/v1/auth/me/"
CAMBIAR = "/api/v1/auth/cambiar-contrasena/"


class Api:
    """Cliente con verificación CSRF real, como un navegador."""

    def __init__(self, **defaults):
        self.client = Client(enforce_csrf_checks=True, **defaults)
        self.token = None

    def csrf(self, **extra):
        response = self.client.get(CSRF, **extra)
        self.token = response.json()["csrfToken"]
        return response

    def get(self, path, **extra):
        return self.client.get(path, **extra)

    def post(self, path, data=None, *, csrf=True, **extra):
        if csrf and self.token is None:
            self.csrf()
        headers = {"HTTP_X_CSRFTOKEN": self.token} if csrf else {}
        return self.client.post(
            path, data or {}, content_type="application/json", **headers, **extra
        )

    def login(self, correo, contrasena=DEFAULT_PASSWORD, **extra):
        response = self.post(LOGIN, {"correo": correo, "contrasena": contrasena}, **extra)
        if response.status_code == 200:
            self.csrf(**extra)  # el token CSRF rota al iniciar sesión
        return response

    @property
    def session_cookie(self):
        morsel = self.client.cookies.get("sessionid")
        return morsel.value if morsel else None


@pytest.fixture
def api():
    return Api()


@pytest.fixture
def usuario(db):
    return UsuarioFactory(correo="ana@empresa.test", nombre="Ana Pérez", rol=Usuario.Rol.ADMIN)


@pytest.fixture
def relaxed_rate(settings):
    """Quita el límite de tasa para probar bloqueo y respuestas sin que estorbe el 429."""
    settings.LOGIN_RATE_PER_IP_EMAIL = 1000
    settings.LOGIN_RATE_PER_IP = 1000


@pytest.fixture
def cuentas(db):
    """Un caso por cada razón por la que un login puede fallar."""
    UsuarioFactory(correo="activo@empresa.test")
    UsuarioFactory(correo="inactivo@empresa.test", activo=False)
    UsuarioFactory(
        correo="bloqueado@empresa.test", bloqueado_hasta=timezone.now() + timedelta(minutes=10)
    )
    sin_clave = UsuarioFactory(correo="sinclave@empresa.test")
    sin_clave.set_unusable_password()
    sin_clave.save()


CASOS = {
    "correo inexistente": ("nadie@empresa.test", DEFAULT_PASSWORD),
    "contraseña incorrecta": ("activo@empresa.test", "otra-contrasena-1"),
    "usuario inactivo": ("inactivo@empresa.test", DEFAULT_PASSWORD),
    "cuenta bloqueada (contraseña correcta)": ("bloqueado@empresa.test", DEFAULT_PASSWORD),
    "sin contraseña utilizable": ("sinclave@empresa.test", DEFAULT_PASSWORD),
}
