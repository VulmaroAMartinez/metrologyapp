"""Candados de las operaciones de administración de usuarios.

Orden global (siempre en este sentido; nunca se pide uno anterior teniendo uno posterior):

- L0: candado consultivo de transacción `pg_advisory_xact_lock(NAMESPACE, ADMINISTRADORES)`.
  Representa "el conjunto de administradores": serializa toda operación que puede cambiar quién
  es administrador activo y es lo que garantiza "nunca queda el sistema sin un administrador".
- L1: fila del usuario, `FOR NO KEY UPDATE`. El login bloquea la suya con NOWAIT; una operación de
  administración bloquea SOLO la fila del objetivo (con espera acotada por lock_timeout).
- L2: la inserción del evento (la llave foránea toma KEY SHARE sobre el actor, que no choca con
  NO KEY UPDATE).

El login nunca toma L0, y las operaciones de administración no bloquean la fila del actor ni la de
los demás administradores: por eso el login de un administrador no choca con lo que hace el otro.

REGLA: toda ruta que escriba `rol` o `activo` de un usuario toma L0 (ver
`tests/test_admin_lock_rule.py`, que lo comprueba con ast). Mientras se tiene L0 se leen los
administradores sin candado de fila: es válido solo si nada más cambia esos dos campos.
"""

from django.conf import settings
from django.db import connection

# Espacio de nombres de los candados consultivos de esta aplicación ("METR" en ASCII) y número de
# cada candado dentro de él. Dos enteros de 32 bits: no colisionan con otros usos de la base.
NAMESPACE_DE_CANDADOS = 0x4D455452
CANDADO_ADMINISTRADORES = 1


def fijar_lock_timeout() -> None:
    """Limita la espera de candados en esta transacción (SET LOCAL). Al vencer, PostgreSQL lanza
    55P03 y la transacción entera se revierte sin dejar estado parcial."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('lock_timeout', %s, true)", [f"{settings.ADMIN_LOCK_TIMEOUT_MS}ms"]
        )


def tomar_candado_de_administradores() -> None:
    """L0. Dura hasta el fin de la transacción."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            [NAMESPACE_DE_CANDADOS, CANDADO_ADMINISTRADORES],
        )
