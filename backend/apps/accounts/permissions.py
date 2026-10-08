"""Permisos por rol (DEFINICION §3), siempre verificados en el backend.

Una única tabla rol -> acciones (`ACCIONES_POR_ROL`) y una sola clase DRF reutilizable,
`requiere(Accion.X)`. Toda vista declara su permiso de forma explícita; si no declara
ninguno, `DenegarPorDefecto` la deniega.

Política de respuesta, igual para todos los recursos: sin sesión -> 401 `no_autenticado`;
con sesión pero sin la acción -> 403 `permiso_denegado` (nunca 404 para ocultar recursos);
cambio de contraseña pendiente -> 403 `debe_cambiar_contrasena`.
"""

import logging
from enum import StrEnum
from types import MappingProxyType

from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.permissions import BasePermission

from .models import Usuario

logger = logging.getLogger(__name__)

CODIGO_CAMBIO_OBLIGATORIO = "debe_cambiar_contrasena"
CODIGO_PERMISO_DENEGADO = "permiso_denegado"


class Accion(StrEnum):
    """Acciones del sistema. Cada fila de la tabla de §3 es una o más acciones."""

    CONSULTAR = "CONSULTAR"  # instrumentos, calibraciones, certificados, timeline, dashboard...
    GENERAR_REPORTES = "GENERAR_REPORTES"  # §3 los incluye en la consulta; §7: los 3 roles
    ESCRIBIR_INSTRUMENTOS = "ESCRIBIR_INSTRUMENTOS"  # crear/editar, estado, área, baja, calibrar...
    GESTIONAR_AVISOS = "GESTIONAR_AVISOS"  # crear, editar y cancelar avisos
    ELIMINAR_CERTIFICADO = "ELIMINAR_CERTIFICADO"  # con motivo
    VER_BITACORA = "VER_BITACORA"  # bitácora de auditoría completa
    GESTIONAR_USUARIOS = "GESTIONAR_USUARIOS"
    GESTIONAR_CATALOGOS = "GESTIONAR_CATALOGOS"  # catálogos y configuración [PROPUESTA]
    RECIBIR_CORREOS_CADUCIDAD = "RECIBIR_CORREOS_CADUCIDAD"  # lo usa el proceso diario (sprint 4)


_ROL = Usuario.Rol

# Lista explícita por rol (no "todas las acciones" para el administrador): una acción nueva
# que no se agregue aquí queda denegada para todos hasta que alguien decida quién puede.
ACCIONES_POR_ROL = MappingProxyType(
    {
        _ROL.ADMIN: frozenset(
            {
                Accion.CONSULTAR,
                Accion.GENERAR_REPORTES,
                Accion.ESCRIBIR_INSTRUMENTOS,
                Accion.GESTIONAR_AVISOS,
                Accion.ELIMINAR_CERTIFICADO,
                Accion.VER_BITACORA,
                Accion.GESTIONAR_USUARIOS,
                Accion.GESTIONAR_CATALOGOS,
                Accion.RECIBIR_CORREOS_CADUCIDAD,
            }
        ),
        _ROL.TECNICO: frozenset(
            {
                Accion.CONSULTAR,
                Accion.GENERAR_REPORTES,
                Accion.ESCRIBIR_INSTRUMENTOS,
                Accion.GESTIONAR_AVISOS,
                Accion.RECIBIR_CORREOS_CADUCIDAD,
            }
        ),
        _ROL.ESPECTADOR: frozenset({Accion.CONSULTAR, Accion.GENERAR_REPORTES}),
    }
)


def puede(rol, accion) -> bool:
    return accion in ACCIONES_POR_ROL.get(rol, frozenset())


class PasswordChangeNotRequired(BasePermission):
    """Mientras `debe_cambiar_contrasena` sea verdadero, todo responde 403 con un código claro.

    Las vistas que deben seguir accesibles (me, logout, cambiar-contrasena) no la incluyen.
    """

    def has_permission(self, request, view):
        if getattr(request.user, "debe_cambiar_contrasena", False):
            raise PermissionDenied(
                {
                    "codigo": CODIGO_CAMBIO_OBLIGATORIO,
                    "detalle": "Debes cambiar tu contraseña antes de continuar.",
                }
            )
        return True


def _autenticado(request) -> bool:
    return bool(request.user and request.user.is_authenticated)


class DenegarPorDefecto(BasePermission):
    """Permiso por defecto de DRF: una vista que no declara permisos queda denegada."""

    def has_permission(self, request, view):
        if not _autenticado(request):
            raise NotAuthenticated()
        logger.warning("Vista sin permisos declarados: %s %s", request.method, request.path)
        raise PermissionDenied(
            {"codigo": CODIGO_PERMISO_DENEGADO, "detalle": "No tienes permiso para esta acción."}
        )


class RequiereAccion(BasePermission):
    """Sesión válida + sin cambio de contraseña pendiente + rol con la acción requerida."""

    accion_requerida: Accion

    def has_permission(self, request, view):
        if not _autenticado(request):
            raise NotAuthenticated()
        PasswordChangeNotRequired().has_permission(request, view)
        if puede(request.user.rol, self.accion_requerida):
            return True
        # Sin evento (§6.9 no lo define): solo una línea de log, sin datos sensibles.
        logger.warning(
            "Permiso denegado: usuario_id=%s rol=%s accion=%s metodo=%s ruta=%s",
            request.user.pk,
            request.user.rol,
            self.accion_requerida.value,
            request.method,
            request.path,
        )
        raise PermissionDenied(
            {"codigo": CODIGO_PERMISO_DENEGADO, "detalle": "No tienes permiso para esta acción."}
        )


def requiere(accion: Accion) -> type[RequiereAccion]:
    """Clase de permiso DRF para `@permission_classes([requiere(Accion.X)])`."""
    if not isinstance(accion, Accion):
        raise TypeError("requiere() espera un miembro de Accion.")
    return type(f"Requiere_{accion.value}", (RequiereAccion,), {"accion_requerida": accion})
