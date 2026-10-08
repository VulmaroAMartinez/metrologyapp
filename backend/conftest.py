import os
import subprocess
import sys
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
PRIVILEGES_SQL = Path(os.environ.get("PRIVILEGES_SQL", "/app/db/privileges.sql"))
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
        subprocess.run(  # noqa: S603
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
