"""Ayudantes de prueba compartidos por todas las apps (NO es código de producción).

Un solo lugar para lo que usan las pruebas de más de una app: el cliente con CSRF real, la fábrica
de usuarios, las rutas de autenticación y las herramientas para pruebas con hilos. Las fixtures
comunes viven en el `conftest.py` raíz; ninguna app importa del `conftest.py` de otra.
"""

import os
import threading
import time

import factory
import psycopg
import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import Client, RequestFactory

from apps.accounts.models import Usuario

CSRF = "/api/v1/auth/csrf/"
LOGIN = "/api/v1/auth/login/"
LOGOUT = "/api/v1/auth/logout/"
ME = "/api/v1/auth/me/"
CAMBIAR = "/api/v1/auth/cambiar-contrasena/"

DEFAULT_PASSWORD = "contrasena-de-prueba-1"
TOPE = 10  # segundos: solo para que una prueba con hilos rota falle en vez de colgarse


# --- usuarios ---------------------------------------------------------------------------------


class UsuarioFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Usuario
        skip_postgeneration_save = True

    nombre = factory.Faker("name")
    correo = factory.Sequence(lambda n: f"usuario{n}@empresa.test")
    rol = Usuario.Rol.TECNICO

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        obj.set_password(extracted or DEFAULT_PASSWORD)
        if create:
            obj.save()


# --- cliente HTTP -----------------------------------------------------------------------------


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

    def call(self, metodo, path, data=None, *, csrf=True, **extra):
        """Cualquier método con CSRF válido (GET no lo necesita)."""
        if csrf and self.token is None:
            self.csrf()
        headers = {"HTTP_X_CSRFTOKEN": self.token} if csrf else {}
        enviar = getattr(self.client, metodo.lower())
        if metodo.upper() == "GET":
            return enviar(path, **headers, **extra)
        return enviar(path, data or {}, content_type="application/json", **headers, **extra)

    def login(self, correo, contrasena=DEFAULT_PASSWORD, **extra):
        response = self.post(LOGIN, {"correo": correo, "contrasena": contrasena}, **extra)
        if response.status_code == 200:
            self.csrf(**extra)  # el token CSRF rota al iniciar sesión
        return response

    @property
    def session_cookie(self):
        morsel = self.client.cookies.get("sessionid")
        return morsel.value if morsel else None


def sesion_directa(usuario) -> Api:
    """Sesión abierta SIN pasar por el login auditado: no deja eventos propios en la bitácora,
    así cada prueba controla exactamente qué eventos existen."""
    api = Api()
    api.client.force_login(usuario)
    api.csrf()
    return api


def peticion():
    """Una petición con sesión, como la que recibe el login."""
    solicitud = RequestFactory().post(LOGIN)
    solicitud.session = SessionStore()
    return solicitud


# --- hilos ------------------------------------------------------------------------------------


class Retencion:
    """Deja a un hilo detenido en un punto hasta que el hilo principal lo suelta."""

    def __init__(self):
        self.dentro = threading.Event()  # el hilo retenido ya llegó al punto
        self.seguir = threading.Event()  # el hilo principal lo suelta


def en_hilo(nombre, funcion):
    """Ejecuta `funcion` en un hilo con su propia conexión; devuelve (hilo, resultado), donde
    `resultado` tendrá `valor` o `error`."""
    resultado = {}

    def correr():
        try:
            resultado["valor"] = funcion()
        except Exception as error:  # noqa: BLE001 - se devuelve para afirmarlo desde la prueba
            resultado["error"] = error
        finally:
            connection.close()

    hilo = threading.Thread(target=correr, name=nombre)
    hilo.start()
    return hilo, resultado


def esperar_bloqueo():
    """Sondeo acotado: espera a que PostgreSQL tenga un backend esperando un candado."""
    limite = time.monotonic() + TOPE
    pausa = threading.Event()
    while time.monotonic() < limite:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return
        pausa.wait(0.005)
    pytest.fail("ningún hilo llegó a esperar un candado")


# --- base de datos ----------------------------------------------------------------------------


def conectar_bd(usuario: str, clave: str, nombre: str) -> psycopg.Connection:
    """Conexión directa (autocommit) a una base del servicio `db`, con el rol que se indique."""
    return psycopg.connect(
        host=os.environ.get("DB_HOST", "db"),
        dbname=nombre,
        user=usuario,
        password=clave,
        autocommit=True,
    )
