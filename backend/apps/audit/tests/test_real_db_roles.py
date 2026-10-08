"""La tabla de eventos, vista con el rol `app` sobre la base REAL (la de init.sql).

Solo inserción también en la base de datos: `app` hace INSERT y SELECT; UPDATE, DELETE y
TRUNCATE fallan. La migración 0001 de audit los revoca (los privilegios por defecto los
darían). La base de pytest (`test_*`) no tiene esos privilegios por defecto, por eso se usa
la real, migrada antes con el rol migrator.
"""

import os
import subprocess
import sys
import uuid
from contextlib import closing
from pathlib import Path

import pytest
from psycopg import errors

from apps.core.tests.test_db_roles import connect

BACKEND_DIR = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def base_real_migrada():
    """Aplica las migraciones a la base real como migrator (idempotente)."""
    subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "migrate", "--noinput"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.dev"},
        check=True,
        capture_output=True,
    )


@pytest.fixture
def app(base_real_migrada):
    with closing(connect("app", os.environ["APP_DB_PASSWORD"])) as conn:
        yield conn


@pytest.fixture
def marca(base_real_migrada):
    """Etiqueta única para las filas de esta prueba; las limpia migrator (dueño) al terminar."""
    etiqueta = f"prueba-roles-{uuid.uuid4().hex[:10]}"
    yield etiqueta
    with closing(connect(os.environ["DB_USER"], os.environ["DB_PASSWORD"])) as conn:
        conn.execute("DELETE FROM audit_evento WHERE descripcion = %s", (etiqueta,))


def insertar(app, marca):
    app.execute(
        "INSERT INTO audit_evento (ocurrido_en, tipo, descripcion) "
        "VALUES (now(), 'SESION_FALLIDA', %s)",
        (marca,),
    )


def test_app_can_insert_and_select_events(app, marca):
    insertar(app, marca)

    filas = app.execute(
        "SELECT tipo, usuario_id FROM audit_evento WHERE descripcion = %s", (marca,)
    ).fetchall()

    assert filas == [("SESION_FALLIDA", None)]


@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE audit_evento SET descripcion = 'alterada' WHERE descripcion = %s",
        "UPDATE audit_evento SET descripcion = 'alterada'",
        "DELETE FROM audit_evento WHERE descripcion = %s",
        "DELETE FROM audit_evento",
        "TRUNCATE audit_evento",
    ],
)
def test_app_cannot_update_delete_or_truncate_events(app, marca, sentencia):
    insertar(app, marca)
    parametros = (marca,) if "%s" in sentencia else None

    with pytest.raises(errors.InsufficientPrivilege):
        app.execute(sentencia, parametros)

    # La fila sigue intacta.
    filas = app.execute(
        "SELECT descripcion FROM audit_evento WHERE descripcion = %s", (marca,)
    ).fetchall()
    assert filas == [(marca,)]


def test_privileges_as_reported_by_postgres(app):
    consulta = "SELECT has_table_privilege('app', 'audit_evento', %s)"
    permisos = {
        p: app.execute(consulta, (p,)).fetchone()[0]
        for p in ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]
    }

    assert permisos == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": False,
        "DELETE": False,
        "TRUNCATE": False,
    }


def test_app_cannot_alter_the_events_table(app):
    for sentencia in ("ALTER TABLE audit_evento ADD COLUMN extra int", "DROP TABLE audit_evento"):
        with pytest.raises(errors.InsufficientPrivilege):
            app.execute(sentencia)
