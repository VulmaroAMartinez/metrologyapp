"""Migraciones con datos reales: accounts.0002, audit.0003, accounts.0004 y accounts.0005.

Cada prueba crea una base propia (como `migrator`, el dueño), migra hasta el estado ANTERIOR con
`manage.py migrate`, inserta filas con SQL, aplica la migración y comprueba qué pasó con los
datos. Si la migración falla, PostgreSQL revierte su DDL: la base queda en el estado anterior.
"""

import os
import subprocess
import sys
import uuid
from contextlib import closing
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

BACKEND_DIR = Path(__file__).resolve().parents[3]
HOST = os.environ.get("DB_HOST", "db")


def conectar(nombre):
    return psycopg.connect(
        host=HOST,
        dbname=nombre,
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        autocommit=True,
    )


class BaseDeMigraciones:
    def __init__(self, nombre):
        self.nombre = nombre

    def migrar(self, app, objetivo):
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings.dev",
            "DB_NAME": self.nombre,
        }
        return subprocess.run(  # noqa: S603
            [sys.executable, "manage.py", "migrate", app, objetivo, "--noinput"],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def aplicadas(self, app):
        with closing(conectar(self.nombre)) as conn:
            filas = conn.execute("SELECT name FROM django_migrations WHERE app = %s", (app,))
            return {nombre for (nombre,) in filas.fetchall()}

    def consulta(self, sql, parametros=None):
        with closing(conectar(self.nombre)) as conn:
            return conn.execute(sql, parametros).fetchall()

    def ejecutar(self, sql, parametros=None):
        with closing(conectar(self.nombre)) as conn:
            conn.execute(sql, parametros)

    def columnas(self, tabla):
        filas = self.consulta(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (tabla,)
        )
        return {nombre for (nombre,) in filas}


@pytest.fixture
def base():
    nombre = f"metrologia_mig_{uuid.uuid4().hex[:8]}"
    with closing(conectar("postgres")) as admin:
        admin.execute(f"CREATE DATABASE {nombre} OWNER migrator")
    try:
        yield BaseDeMigraciones(nombre)
    finally:
        with closing(conectar("postgres")) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")


def migrar_bien(base, app, objetivo):
    resultado = base.migrar(app, objetivo)
    assert resultado.returncode == 0, resultado.stderr
    return resultado


USUARIO_0001 = (
    "INSERT INTO accounts_usuario (password, nombre, numero_nomina, correo, rol, activo, "
    "intentos_fallidos, debe_cambiar_contrasena, creado_en) "
    "VALUES (%s, %s, %s, %s, %s, true, 0, %s, now())"
)


def insertar_usuario(base, correo, *, nomina="", rol="TECNICO", cambio=False, nombre=None):
    base.ejecutar(
        USUARIO_0001,
        (f"hash-de-{correo}", nombre or correo.split("@")[0], nomina, correo, rol, cambio),
    )


# --- accounts.0002: numero_nomina nulo y único --------------------------------------------------


def test_accounts_0002_turns_empty_payroll_numbers_into_null_and_keeps_the_rest(base):
    migrar_bien(base, "accounts", "0001")
    for correo, nomina in [
        ("a@e.test", ""),
        ("b@e.test", ""),
        ("c@e.test", "   "),  # solo espacios: también es «sin nómina»
        ("d@e.test", "N-1"),
        ("e@e.test", " N-2 "),  # con espacios: la migración no la toca
    ]:
        insertar_usuario(base, correo, nomina=nomina)

    migrar_bien(base, "accounts", "0002")

    filas = dict(base.consulta("SELECT correo, numero_nomina FROM accounts_usuario"))
    assert filas["a@e.test"] is None
    assert filas["b@e.test"] is None
    assert filas["c@e.test"] is None
    assert filas["d@e.test"] == "N-1"
    assert filas["e@e.test"] == " N-2 "
    # Muchos NULL conviven; un valor repetido no.
    base.ejecutar(USUARIO_0002_NULO, ("hash", "f", "f@e.test"))
    base.ejecutar(USUARIO_0002_NULO, ("hash", "g", "g@e.test"))
    with pytest.raises(errors.UniqueViolation, match="usuario_nomina_unica"):
        base.ejecutar("UPDATE accounts_usuario SET numero_nomina = 'N-1' WHERE correo = 'f@e.test'")


USUARIO_0002_NULO = (
    "INSERT INTO accounts_usuario (password, nombre, numero_nomina, correo, rol, activo, "
    "intentos_fallidos, debe_cambiar_contrasena, creado_en) "
    "VALUES (%s, %s, NULL, %s, 'TECNICO', true, 0, false, now())"
)


def test_accounts_0003_makes_the_payroll_uniqueness_case_insensitive(base):
    migrar_bien(base, "accounts", "0002")
    insertar_usuario(base, "a@e.test", nomina="N-Aa1")
    insertar_usuario(base, "b@e.test", nomina="n-aa1")  # distinta en 0002: sí se permite

    resultado = base.migrar("accounts", "0003")  # con datos que chocan sin distinguir mayúsculas

    assert resultado.returncode != 0
    assert (
        "usuario_nomina_unica" in resultado.stderr or "could not create unique" in resultado.stderr
    )
    assert "0003_usuarios_nuevos_tipos_y_nomina_ci" not in base.aplicadas("accounts")
    base.ejecutar("UPDATE accounts_usuario SET numero_nomina = 'otra' WHERE correo = 'b@e.test'")
    migrar_bien(base, "accounts", "0003")
    with pytest.raises(errors.UniqueViolation, match="usuario_nomina_unica"):
        base.ejecutar(
            "UPDATE accounts_usuario SET numero_nomina = 'N-AA1' WHERE correo = 'b@e.test'"
        )


def test_accounts_0002_fails_cleanly_when_existing_payroll_numbers_are_duplicated(base):
    migrar_bien(base, "accounts", "0001")
    insertar_usuario(base, "a@e.test", nomina="N-1")
    insertar_usuario(base, "b@e.test", nomina="N-1")

    resultado = base.migrar("accounts", "0002")

    assert resultado.returncode != 0
    assert "0002_numero_nomina_nulo_y_unico" not in base.aplicadas(
        "accounts"
    )  # sin estado a medias
    antes = base.consulta("SELECT count(*) FROM accounts_usuario WHERE numero_nomina = 'N-1'")
    assert antes == [(2,)]  # los datos siguen intactos


# --- audit.0003: se quitan los tipos AVISO_* -----------------------------------------------------

EVENTO = "INSERT INTO audit_evento (ocurrido_en, tipo, descripcion) VALUES (now(), %s, %s)"


def test_audit_0003_refuses_to_run_with_notice_events_and_runs_without_them(base):
    migrar_bien(base, "audit", "0002")
    base.ejecutar(EVENTO, ("USUARIO_CREADO", "se conserva"))
    base.ejecutar(EVENTO, ("AVISO_ENVIADO", "de un aviso"))
    base.ejecutar(EVENTO, ("AVISO_FALLIDO", "de otro aviso"))

    resultado = base.migrar("audit", "0003")

    assert resultado.returncode != 0
    assert "2 eventos con tipo AVISO_" in resultado.stderr
    assert "0003_quitar_tipos_aviso" not in base.aplicadas("audit")
    assert base.consulta("SELECT count(*) FROM audit_evento") == [(3,)]  # nada se borró

    # El dueño resuelve esas filas (los eventos no se borran desde la aplicación, que es `app`).
    base.ejecutar("DELETE FROM audit_evento WHERE tipo LIKE 'AVISO\\_%'")
    migrar_bien(base, "audit", "0003")

    assert base.consulta("SELECT tipo, descripcion FROM audit_evento") == [
        ("USUARIO_CREADO", "se conserva")
    ]
    with pytest.raises(errors.CheckViolation, match="evento_tipo_valido"):
        base.ejecutar(EVENTO, ("AVISO_CREADO", "ya no existe"))
    base.ejecutar(EVENTO, ("PROCESO_DIARIO_EJECUTADO", "se conserva el tipo del proceso diario"))


def test_audit_0003_runs_with_only_valid_events(base):
    migrar_bien(base, "audit", "0002")
    for tipo in ("SESION_INICIADA", "SESION_FALLIDA", "CUENTA_BLOQUEADA", "USUARIO_EDITADO"):
        base.ejecutar(EVENTO, (tipo, tipo.lower()))

    migrar_bien(base, "audit", "0003")

    assert base.consulta("SELECT count(*) FROM audit_evento") == [(4,)]


# --- accounts.0004: se quita el cambio forzado de contraseña -------------------------------------


def test_accounts_0004_drops_the_forced_change_column_and_keeps_every_user(base):
    migrar_bien(base, "accounts", "0003")
    assert "debe_cambiar_contrasena" in base.columnas("accounts_usuario")
    insertar_usuario(base, "a@e.test", nomina="N-1", rol="ADMIN", cambio=False, nombre="Ana")
    insertar_usuario(base, "b@e.test", rol="TECNICO", cambio=True, nombre="Beto")
    antes = base.consulta(
        "SELECT correo, nombre, rol, numero_nomina, password, activo FROM accounts_usuario "
        "ORDER BY correo"
    )

    migrar_bien(base, "accounts", "0004")

    assert "debe_cambiar_contrasena" not in base.columnas("accounts_usuario")
    despues = base.consulta(
        "SELECT correo, nombre, rol, numero_nomina, password, activo FROM accounts_usuario "
        "ORDER BY correo"
    )
    assert despues == antes  # ni un dato de ningún usuario cambió


def test_accounts_0004_can_be_reversed_and_the_column_comes_back_false(base):
    migrar_bien(base, "accounts", "0003")
    insertar_usuario(base, "b@e.test", cambio=True)
    migrar_bien(base, "accounts", "0004")

    migrar_bien(base, "accounts", "0003")  # marcha atrás

    assert "debe_cambiar_contrasena" in base.columnas("accounts_usuario")
    assert base.consulta("SELECT debe_cambiar_contrasena FROM accounts_usuario") == [(False,)]


# --- accounts.0005: correo y numero_nomina solo ASCII --------------------------------------------


def insertar_0004(base, correo, nomina=None):
    base.ejecutar(
        "INSERT INTO accounts_usuario (password, nombre, numero_nomina, correo, rol, activo, "
        "intentos_fallidos, creado_en) VALUES ('h', 'x', %s, %s, 'TECNICO', true, 0, now())",
        (nomina, correo),
    )


def test_accounts_0005_refuses_to_run_with_non_ascii_users_and_then_enforces_ascii(base):
    migrar_bien(base, "accounts", "0004")
    insertar_0004(base, "ok@e.test", "N-1")
    insertar_0004(base, "josé@e.test")
    insertar_0004(base, "luis@e.test", "Ñ-7")

    resultado = base.migrar("accounts", "0005")

    assert resultado.returncode != 0
    assert "2 usuarios con caracteres no ASCII" in resultado.stderr
    assert "0005_correo_y_nomina_solo_ascii" not in base.aplicadas("accounts")
    base.ejecutar("UPDATE accounts_usuario SET correo = 'jose@e.test' WHERE correo LIKE 'jos%'")
    base.ejecutar("UPDATE accounts_usuario SET numero_nomina = NULL WHERE correo = 'luis@e.test'")

    migrar_bien(base, "accounts", "0005")

    assert base.consulta("SELECT count(*) FROM accounts_usuario") == [(3,)]
    with pytest.raises(errors.CheckViolation, match="usuario_correo_ascii"):
        insertar_0005(base, "más@e.test")
    with pytest.raises(errors.CheckViolation, match="usuario_nomina_ascii"):
        insertar_0005(base, "mas@e.test", "Ñ")


def insertar_0005(base, correo, nomina=None):
    insertar_0004(base, correo, nomina)


# --- accounts.0006: sesion_version ----------------------------------------------------------------


def test_accounts_0006_adds_the_session_version_at_zero_and_keeps_every_user(base):
    migrar_bien(base, "accounts", "0005")
    insertar_0004(base, "a@e.test", "N-1")
    insertar_0004(base, "b@e.test")
    assert "sesion_version" not in base.columnas("accounts_usuario")
    consulta = (
        "SELECT correo, nombre, rol, numero_nomina, password, activo "
        "FROM accounts_usuario ORDER BY correo"
    )
    antes = base.consulta(consulta)

    migrar_bien(base, "accounts", "0006")

    assert "sesion_version" in base.columnas("accounts_usuario")
    assert base.consulta("SELECT sesion_version FROM accounts_usuario") == [(0,), (0,)]
    assert base.consulta(consulta) == antes  # ni un dato de ningún usuario cambió
    base.ejecutar(
        "UPDATE accounts_usuario SET sesion_version = sesion_version + 1"
    )  # atómico, sin NULL
    assert base.consulta("SELECT sesion_version FROM accounts_usuario") == [(1,), (1,)]


def test_accounts_0006_can_be_reversed(base):
    migrar_bien(base, "accounts", "0006")

    migrar_bien(base, "accounts", "0005")

    assert "sesion_version" not in base.columnas("accounts_usuario")
