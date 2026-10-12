"""Reglas de negocio de las cuentas. Las vistas y los comandos solo validan forma y delegan aquí.

- `autenticacion`: inicio y cierre de sesión (un fallo de login NO lanza: se confirma con un 401).
- `usuarios`: gestión de usuarios (solo ADMIN), cambio de la contraseña propia y errores de negocio.

Los eventos se escriben en la misma transacción que el cambio (apps.audit). Aquí se reexporta lo
público para usar `from apps.accounts import services` y `services.nombre`.
"""

from . import autenticacion, usuarios
from .autenticacion import (
    ETIQUETA_MOTIVO,
    MotivoFallo,
    ResultadoLogin,
    cerrar_sesion,
    dummy_hash,
    iniciar_sesion,
)
from .usuarios import (
    CORREO_MAX,
    NOMBRE_MAX,
    NOMINA_MAX,
    ActorNoVigente,
    AutoModificacionNoPermitida,
    ContrasenaActualIncorrecta,
    ContrasenaModificada,
    ContrasenaRepetida,
    CorreoDuplicado,
    DatosDeUsuarioInvalidos,
    ErrorDeUsuario,
    NominaDuplicada,
    ReintentarMasTarde,
    UltimoAdministrador,
    UsuarioInexistente,
    cambiar_contrasena,
    cambiar_rol,
    crear_usuario,
    desactivar_usuario,
    editar_usuario,
    invalidar_sesiones,
    limpiar_campos,
    reactivar_usuario,
    restablecer_contrasena,
    validar_contrasena,
)

__all__ = [
    "CORREO_MAX",
    "ETIQUETA_MOTIVO",
    "NOMBRE_MAX",
    "NOMINA_MAX",
    "ActorNoVigente",
    "AutoModificacionNoPermitida",
    "ContrasenaActualIncorrecta",
    "ContrasenaModificada",
    "ContrasenaRepetida",
    "CorreoDuplicado",
    "DatosDeUsuarioInvalidos",
    "ErrorDeUsuario",
    "MotivoFallo",
    "NominaDuplicada",
    "ReintentarMasTarde",
    "ResultadoLogin",
    "UltimoAdministrador",
    "UsuarioInexistente",
    "autenticacion",
    "cambiar_contrasena",
    "cambiar_rol",
    "cerrar_sesion",
    "crear_usuario",
    "desactivar_usuario",
    "dummy_hash",
    "editar_usuario",
    "iniciar_sesion",
    "invalidar_sesiones",
    "limpiar_campos",
    "reactivar_usuario",
    "restablecer_contrasena",
    "usuarios",
    "validar_contrasena",
]
