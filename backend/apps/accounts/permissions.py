from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

CODIGO_CAMBIO_OBLIGATORIO = "debe_cambiar_contrasena"


class PasswordChangeNotRequired(BasePermission):
    """Mientras `debe_cambiar_contrasena` sea verdadero, todo responde 403 con un código claro.

    Las vistas que deben seguir accesibles (me, logout, cambiar-contrasena) la omiten en su
    `permission_classes`.
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
