"""Lista blanca de campos que pueden entrar a `antes` / `despues` de un evento.

Nunca contraseñas, hashes, tokens ni contadores internos: cualquier clave fuera de la lista
se rechaza (no se descarta en silencio, para que el error se vea al desarrollar). Cada sprint
que audita datos nuevos agrega aquí sus campos de negocio.
"""

import re
from collections.abc import Mapping
from datetime import date, datetime

CAMPOS_PERMITIDOS = frozenset(
    {
        "nombre",
        "correo",
        "numero_nomina",
        "rol",
        "activo",
        "debe_cambiar_contrasena",
        "bloqueado_hasta",
        "motivo",
    }
)

# Defensa en profundidad: ni siquiera un campo permitido puede contener algo con forma de hash.
_FORMA_DE_HASH = re.compile(r"^(argon2|pbkdf2_\w+|bcrypt\w*|scrypt|md5|sha\d+)\$", re.IGNORECASE)


class DatosEventoInvalidosError(ValueError):
    pass


def sanear(datos, nombre: str):
    if datos is None:
        return None
    if not isinstance(datos, Mapping):
        raise DatosEventoInvalidosError(f"'{nombre}' debe ser un diccionario o None.")
    sobrantes = sorted(str(clave) for clave in datos if clave not in CAMPOS_PERMITIDOS)
    if sobrantes:
        raise DatosEventoInvalidosError(
            f"Campos no permitidos en '{nombre}': {', '.join(sobrantes)}."
        )
    return {clave: _valor(clave, valor) for clave, valor in datos.items()}


def _valor(clave, valor):
    if isinstance(valor, datetime | date):
        return valor.isoformat()
    if valor is None or isinstance(valor, bool | int | float):
        return valor
    if isinstance(valor, str):
        if _FORMA_DE_HASH.match(valor):
            raise DatosEventoInvalidosError(f"El valor de '{clave}' tiene forma de hash.")
        return valor
    raise DatosEventoInvalidosError(f"El valor de '{clave}' debe ser un escalar.")


def solo_permitidos(datos):
    """Defensa en profundidad al LEER: aunque una fila antigua o insertada por otra vía tuviera
    claves fuera de la lista blanca, la bitácora nunca las muestra."""
    if not isinstance(datos, Mapping):
        return None if datos is None else {}
    return {clave: valor for clave, valor in datos.items() if clave in CAMPOS_PERMITIDOS}
