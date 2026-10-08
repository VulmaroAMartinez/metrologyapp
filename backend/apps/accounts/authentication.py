from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied


class StrictSessionAuthentication(SessionAuthentication):
    """Sesión + CSRF también para peticiones anónimas.

    DRF solo exige CSRF a quien ya tiene sesión; aquí también a quien no la tiene, para que
    login, logout y cualquier POST público lleven token. Las lecturas (GET) no lo requieren.
    """

    def authenticate(self, request):
        resultado = super().authenticate(request)
        if resultado is None:
            self.enforce_csrf(request)
        return resultado

    def enforce_csrf(self, request):
        try:
            super().enforce_csrf(request)
        except PermissionDenied as error:
            raise PermissionDenied(
                {"codigo": "csrf_invalido", "detalle": str(error.detail)}
            ) from error

    def authenticate_header(self, request):
        return "Session"  # sin credenciales -> 401, no 403
