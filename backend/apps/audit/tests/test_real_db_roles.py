"""La tabla de eventos, vista con el rol `app` sobre una base propia (`metrologia_roles_test`).

Solo inserción también en la base de datos: `app` hace INSERT y SELECT; UPDATE, DELETE y
TRUNCATE fallan. La migración 0001 de audit los revoca (los privilegios por defecto los
darían). La base de pytest (`test_*`) no tiene esos privilegios por defecto, así que se usa
una base creada y destruida por la prueba con los mismos privilegios que la real
(conftest.roles_db): la base de desarrollo no se toca.
"""

import json
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


@pytest.fixture
def app(roles_db):
    with closing(connect("app", os.environ["APP_DB_PASSWORD"], roles_db)) as conn:
        yield conn


@pytest.fixture
def marca():
    return f"prueba-roles-{uuid.uuid4().hex[:10]}"


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


# --- el ORM con cada rol: el código bloquea siempre; la base solo protege frente a `app` ----

GUION_ORM = """
import json

import django

django.setup()
from django.db import connection, transaction
from django.db.utils import IntegrityError, ProgrammingError

from apps.audit.models import Evento, EventoInmutableError

evento = Evento.objects.create(tipo="SESION_FALLIDA", descripcion="original")


def intentar(accion):
    try:
        with transaction.atomic():
            accion()
    except EventoInmutableError:
        return "codigo"
    except IntegrityError:
        return "integridad"
    except ProgrammingError as error:
        return "privilegios" if "permission denied" in str(error) else str(error)
    return "permitido"


def sql(sentencia):
    def ejecutar():
        with connection.cursor() as cursor:
            cursor.execute(sentencia, [evento.pk])

    return ejecutar


resultado = {
    "save_con_pk": intentar(
        lambda: Evento(pk=evento.pk, tipo="SESION_FALLIDA", descripcion="alterada").save()
    ),
    "base_manager_update": intentar(
        lambda: Evento._base_manager.filter(pk=evento.pk).update(descripcion="alterada")
    ),
    "base_manager_delete": intentar(lambda: Evento._base_manager.filter(pk=evento.pk).delete()),
    "sql_update": intentar(sql("UPDATE audit_evento SET descripcion = 'alterada' WHERE id = %s")),
    "sql_delete": intentar(sql("DELETE FROM audit_evento WHERE id = %s")),
}
print("RESULTADO=" + json.dumps(resultado))
"""

BLOQUEOS_DEL_CODIGO = {
    "save_con_pk": "integridad",  # INSERT con una pk que ya existe
    "base_manager_update": "codigo",
    "base_manager_delete": "codigo",
}


def correr_orm(roles_db, usuario, clave):
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.dev",
        "DB_NAME": roles_db,
        "DB_USER": usuario,
        "DB_PASSWORD": clave,
    }
    proceso = subprocess.run(  # noqa: S603
        [sys.executable, "-c", GUION_ORM],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proceso.returncode == 0, proceso.stderr
    linea = next(x for x in proceso.stdout.splitlines() if x.startswith("RESULTADO="))
    return json.loads(linea.removeprefix("RESULTADO="))


def test_the_orm_cannot_alter_events_as_the_app_role(roles_db):
    resultado = correr_orm(roles_db, "app", os.environ["APP_DB_PASSWORD"])

    assert resultado == {
        **BLOQUEOS_DEL_CODIGO,
        "sql_update": "privilegios",  # la base lo rechaza aunque se esquive el código
        "sql_delete": "privilegios",
    }


def test_the_orm_guards_hold_as_the_owner_role_but_raw_sql_is_the_accepted_risk(roles_db):
    """`migrator` (dueño) no tiene revocaciones: solo el código y las reglas de uso lo frenan.
    Es el riesgo aceptado en docs/decisiones.md; este rol no se usa en ejecución."""
    resultado = correr_orm(roles_db, os.environ["DB_USER"], os.environ["DB_PASSWORD"])

    assert {k: resultado[k] for k in BLOQUEOS_DEL_CODIGO} == BLOQUEOS_DEL_CODIGO
    assert resultado["sql_update"] == "permitido"
