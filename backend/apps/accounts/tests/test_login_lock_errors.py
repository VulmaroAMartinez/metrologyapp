"""El login solo trata como "intento simultáneo" el error de candado no disponible (55P03).

Cualquier otro error de base de datos debe propagarse: capturarlo todo escondería caídas,
deadlocks o timeouts detrás de un 401 que parecería una credencial incorrecta.
"""

import threading
import time

import pytest
from django.contrib.auth.hashers import check_password
from django.db import OperationalError
from psycopg import errors

from apps.accounts import locks, services
from apps.accounts.services import autenticacion
from apps.audit.models import Evento
from apps.core.testing import UsuarioFactory

from .ayudantes import lanza

CORREO = "ana@empresa.test"


def operational(causa):
    error = OperationalError("fallo de base de datos")
    error.__cause__ = causa
    return error


def test_sqlstate_constant_is_postgres_lock_not_available():
    assert locks.SQLSTATE_LOCK_NOT_AVAILABLE == "55P03"
    assert errors.LockNotAvailable.sqlstate == "55P03"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "causa",
    [
        errors.DeadlockDetected(),  # 40P01
        errors.SerializationFailure(),  # 40001
        errors.QueryCanceled(),  # 57014: statement_timeout
        errors.AdminShutdown(),  # 57P01
        errors.ConnectionFailure(),  # 08006
        errors.InsufficientPrivilege(),  # 42501
        None,  # OperationalError sin causa psycopg
        RuntimeError("otra cosa"),
    ],
    ids=lambda c: type(c).__name__,
)
def test_any_other_operational_error_propagates(causa, usuario_ana):
    with (
        autenticacion.sustituir(obtener_usuario_bloqueado=lanza(operational(causa))),
        pytest.raises(OperationalError, match="fallo de base de datos"),
    ):
        services.iniciar_sesion(None, CORREO, "lo-que-sea-1")

    assert Evento.objects.count() == 0  # no se disfrazó de intento simultáneo


@pytest.mark.django_db
def test_non_operational_database_errors_also_propagate(usuario_ana):
    from django.db import ProgrammingError

    with (
        autenticacion.sustituir(obtener_usuario_bloqueado=lanza(ProgrammingError("x"))),
        pytest.raises(ProgrammingError),
    ):
        services.iniciar_sesion(None, CORREO, "lo-que-sea-1")


@pytest.mark.django_db
def test_the_lock_error_is_recognized_by_its_sqlstate_alone(usuario_ana):
    class ConSqlstate(Exception):
        sqlstate = "55P03"

    with autenticacion.sustituir(obtener_usuario_bloqueado=lanza(operational(ConSqlstate()))):
        resultado = services.iniciar_sesion(None, CORREO, "lo-que-sea-1")

    assert resultado.motivo == services.MotivoFallo.INTENTO_SIMULTANEO


@pytest.mark.django_db(transaction=True)
def test_a_real_postgres_row_lock_raises_55P03_and_is_handled_without_waiting():
    """Sin simulaciones de base de datos: dos logins reales contra la misma cuenta.

    El primero se ralentiza en la verificación del hash (1 s) mientras mantiene su candado;
    PostgreSQL responde 55P03 al segundo, que debe rechazarse sin esperar. Además, el evento
    del intento rechazado inserta una fila con llave foránea al usuario (FOR KEY SHARE): si el
    primero usara FOR UPDATE, esa inserción esperaría hasta que terminara.
    """
    usuario = UsuarioFactory(correo=CORREO)
    dentro_del_candado = threading.Event()
    real = check_password
    primero = {}

    def verificacion(contrasena, encoded):
        if threading.current_thread() is primero["hilo"]:
            dentro_del_candado.set()
            time.sleep(1)  # mantiene el candado mientras "calcula" el hash
        return real(contrasena, encoded)

    def login_lento():
        from django.db import connection

        try:
            primero["resultado"] = services.iniciar_sesion(None, CORREO, "primer-intento-1")
        finally:
            connection.close()

    with autenticacion.sustituir(verificar_hash=verificacion):
        primero["hilo"] = threading.Thread(target=login_lento)
        primero["hilo"].start()
        assert dentro_del_candado.wait(timeout=10)
        inicio = time.monotonic()
        segundo = services.iniciar_sesion(None, CORREO, "segundo-intento-1")
        espera = time.monotonic() - inicio
        primero["hilo"].join(timeout=10)

    assert segundo.usuario is None
    assert segundo.motivo == services.MotivoFallo.INTENTO_SIMULTANEO
    assert espera < 0.5, f"el intento contendido esperó {espera:.2f} s"
    assert primero["resultado"].motivo == services.MotivoFallo.CONTRASENA_INCORRECTA
    motivos = sorted(
        Evento.objects.filter(tipo="SESION_FALLIDA", usuario=usuario).values_list(
            "despues__motivo", flat=True
        )
    )
    assert motivos == ["contrasena_incorrecta", "intento_simultaneo"]


@pytest.fixture
def usuario_ana(db):
    return UsuarioFactory(correo=CORREO)
