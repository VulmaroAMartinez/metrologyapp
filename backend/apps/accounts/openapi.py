"""Extensión de drf-spectacular: describe la autenticación por sesión (cookie) en el esquema."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class StrictSessionAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.authentication.StrictSessionAuthentication"
    name = "cookieAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": "sessionid",
            "description": (
                "Sesión por cookie HttpOnly (en producción `__Host-sessionid`). Todo POST, PATCH "
                "y DELETE, también el anónimo, exige además la cabecera `X-CSRFToken` obtenida de "
                "`GET /api/v1/auth/csrf/`."
            ),
        }
