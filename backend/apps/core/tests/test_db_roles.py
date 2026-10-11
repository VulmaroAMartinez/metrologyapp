"""Roles de BD contra una base propia (`metrologia_roles_test`, ver conftest.roles_db).

`ALTER DEFAULT PRIVILEGES` es por base de datos: la base `test_*` de pytest no los tiene.
La base propia recibe los mismos privilegios que la real (deploy/db/privileges.sql) y se
destruye al terminar; la base de desarrollo no se toca.
"""

import os
import uuid
from contextlib import closing

import pytest
from psycopg import errors

from apps.core.testing import conectar_bd


@pytest.fixture
def migrator(roles_db):
    with closing(conectar_bd(os.environ["DB_USER"], os.environ["DB_PASSWORD"], roles_db)) as conn:
        assert conn.execute("SELECT current_user").fetchone()[0] == "migrator"
        yield conn


@pytest.fixture
def app(roles_db):
    with closing(conectar_bd("app", os.environ["APP_DB_PASSWORD"], roles_db)) as conn:
        yield conn


@pytest.fixture
def table(migrator):
    """Tabla creada por migrator DESPUÉS de init.sql: solo los privilegios por defecto la cubren."""
    name = f"roles_probe_{uuid.uuid4().hex[:8]}"
    migrator.execute(f"CREATE TABLE {name} (id bigint GENERATED ALWAYS AS IDENTITY, v text)")
    yield name
    migrator.execute(f"DROP TABLE IF EXISTS {name}")


def test_app_gets_dml_on_tables_created_by_migrator(app, table):
    app.execute(f"INSERT INTO {table} (v) VALUES ('a'), ('b')")  # noqa: S608
    app.execute(f"UPDATE {table} SET v = 'c' WHERE v = 'a'")  # noqa: S608
    rows = app.execute(f"SELECT v FROM {table} ORDER BY id").fetchall()  # noqa: S608
    app.execute(f"DELETE FROM {table} WHERE v = 'c'")  # noqa: S608
    remaining = app.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608

    assert rows == [("c",), ("b",)]
    assert remaining == 1


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE app_must_not_create (x int)",
        "ALTER TABLE {table} ADD COLUMN extra int",
        "ALTER TABLE {table} RENAME TO stolen",
        "DROP TABLE {table}",
        "TRUNCATE {table}",
        "CREATE INDEX app_idx ON {table} (v)",
        "CREATE SCHEMA app_schema",
    ],
)
def test_app_cannot_change_the_schema(app, table, statement):
    with pytest.raises((errors.InsufficientPrivilege, errors.UndefinedTable)):
        app.execute(statement.format(table=table))


def test_app_is_not_superuser_nor_owner(app):
    row = app.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    owner = app.execute(
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
    ).fetchone()[0]

    assert row == (False, False, False)
    assert owner == "migrator"
