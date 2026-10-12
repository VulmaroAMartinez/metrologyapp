import os
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import psycopg
import pytest


class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    """Reloj controlado para sesiones, bloqueos y ventanas de tasa.

    Django lee la hora con `timezone.now()` (sesiones) y con `tz_now`, enlazado al importar,
    en la caché de base de datos: hay que parchear ambos.
    """
    reloj = Clock(datetime(2026, 10, 8, 12, 0, 30, tzinfo=UTC))
    with (
        mock.patch("django.utils.timezone.now", side_effect=lambda: reloj.now),
        mock.patch("django.core.cache.backends.db.tz_now", side_effect=lambda: reloj.now),
    ):
        yield reloj


# --- base de datos propia para las pruebas de roles ------------------------------------------

ROLES_TEST_DB = "metrologia_roles_test"
PRIVILEGES_SQL = Path(os.environ.get("PRIVILEGES_SQL", "/opt/test/privileges.sql"))
BACKEND_DIR = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def roles_db():
    """Base `metrologia_roles_test`: creada al pedirla y destruida al terminar la sesión.

    Se crea como `migrator`, recibe los MISMOS privilegios que la real (`privileges.sql`, el
    mismo archivo que usa init.sh) y se migra por completo. Así las pruebas de roles no migran
    ni escriben en la base de desarrollo.
    """
    host = os.environ.get("DB_HOST", "db")
    usuario, clave = os.environ["DB_USER"], os.environ["DB_PASSWORD"]

    def conectar(nombre):
        return psycopg.connect(
            host=host, dbname=nombre, user=usuario, password=clave, autocommit=True
        )

    with closing(conectar("postgres")) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {ROLES_TEST_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {ROLES_TEST_DB} OWNER migrator")
    try:
        with closing(conectar(ROLES_TEST_DB)) as conn:
            conn.execute(PRIVILEGES_SQL.read_text(encoding="utf-8"))
        subprocess.run(
            [sys.executable, "manage.py", "migrate", "--noinput"],
            cwd=BACKEND_DIR,
            env={
                **os.environ,
                "DJANGO_SETTINGS_MODULE": "config.settings.dev",
                "DB_NAME": ROLES_TEST_DB,
            },
            check=True,
            capture_output=True,
        )
        yield ROLES_TEST_DB
    finally:
        with closing(conectar("postgres")) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {ROLES_TEST_DB} WITH (FORCE)")


# --- cobertura de los subprocesos (settings.prod se importa en un intérprete nuevo) ---
#
# Varias pruebas ejecutan `python -c "import config.settings.prod"` con un entorno explícito,
# porque la validación de producción ocurre al importar el módulo. Para medir esos procesos,
# coverage necesita arrancar dentro de ellos: un `sitecustomize` llama a
# `coverage.process_startup()`. Va en un directorio temporal (el contenedor es de solo lectura)
# y se pone en PYTHONPATH solo para esos procesos hijos.

_SITIO_COBERTURA = Path(tempfile.gettempdir()) / "cobertura_subprocesos"
_SITIO_COBERTURA.mkdir(exist_ok=True)
(_SITIO_COBERTURA / "sitecustomize.py").write_text(
    "try:\n"
    "    import coverage\n"
    "    coverage.process_startup()\n"
    "except Exception:  # sin coverage: no hay nada que medir\n"
    "    pass\n",
    encoding="utf-8",
)
ENTORNO_COBERTURA = {
    "COVERAGE_PROCESS_START": str(Path(__file__).resolve().parent / "pyproject.toml"),
    "COVERAGE_FILE": os.environ.get(
        "COVERAGE_FILE", str(Path(tempfile.gettempdir()) / ".coverage")
    ),
    "PYTHONPATH": str(_SITIO_COBERTURA),
}


@pytest.fixture(autouse=True)
def _cache_limpia_tras_transacciones_reales(request):
    """El flush de las pruebas con `transaction=True` no vacía `django_cache` (no es un modelo):
    sin esto, los contadores del límite de tasa pasan de una prueba a la siguiente."""
    yield
    marca = request.node.get_closest_marker("django_db")
    if marca is not None and marca.kwargs.get("transaction"):
        from django.core.cache import cache

        cache.clear()


@pytest.fixture(autouse=True)
def _medir_cobertura_de_subprocesos(monkeypatch):
    ejecutar = subprocess.run

    def run(args, *posicionales, env=None, **opciones):
        if env is not None and args and args[0] == sys.executable:
            env = {**env, **ENTORNO_COBERTURA}
        return ejecutar(args, *posicionales, env=env, **opciones)

    monkeypatch.setattr(subprocess, "run", run)


# --- fixtures comunes a las pruebas de varias apps ----------------------------------------------


@pytest.fixture
def api():
    """Cliente HTTP con verificación CSRF real (ver apps/core/testing.py)."""
    from apps.core.testing import Api

    return Api()


@pytest.fixture
def relaxed_rate(settings):
    """Quita el límite de tasa para probar bloqueo y respuestas sin que estorbe el 429."""
    settings.LOGIN_RATE_PER_IP_EMAIL = settings.LOGIN_RATE_PER_IP = 1000


@pytest.fixture
def admin(db):
    from apps.accounts.models import Usuario
    from apps.core.testing import UsuarioFactory

    return UsuarioFactory(correo="admin@empresa.test", nombre="Admin Uno", rol=Usuario.Rol.ADMIN)


@pytest.fixture
def api_admin(admin, relaxed_rate):
    """`admin` con la sesión abierta por el login real (deja sus propios eventos)."""
    from apps.core.testing import Api

    api = Api()
    assert api.login(admin.correo).status_code == 200
    return api


@pytest.fixture
def api_admin_directo(admin):
    """`admin` con la sesión abierta SIN pasar por el login auditado: no deja eventos propios."""
    from apps.core.testing import sesion_directa

    return sesion_directa(admin)


@pytest.fixture
def real_hasher(settings):
    """Argon2 de verdad (las pruebas usan MD5 por velocidad) y un hash ficticio recalculado."""
    from apps.accounts import services
    from config.settings import base

    settings.PASSWORD_HASHERS = base.PASSWORD_HASHERS
    services.dummy_hash.cache_clear()
    yield
    services.dummy_hash.cache_clear()
