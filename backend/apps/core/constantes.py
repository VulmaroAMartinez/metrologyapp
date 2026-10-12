"""Límites compartidos entre modelos, serializadores, servicios y paginación (un solo lugar).

Los de longitud coinciden con las columnas de `accounts.Usuario`; cambiarlos exige una migración.
"""

NOMBRE_MAX = 150
NOMINA_MAX = 20
CORREO_MAX = 254
MAX_PASSWORD_LENGTH = 256  # evita enviar contraseñas enormes a Argon2

TAMANO_POR_DEFECTO = 25  # tamaño de página (usuarios y bitácora)
TAMANO_MAXIMO = 100
