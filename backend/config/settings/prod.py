from .base import *  # noqa: F403
from .guards import validate_production

validate_production(
    secret_key=SECRET_KEY,  # noqa: F405
    db_password=DATABASES["default"]["PASSWORD"],  # noqa: F405
    allowed_hosts=ALLOWED_HOSTS,  # noqa: F405
    debug=DEBUG,  # noqa: F405
    trusted_proxy_cidrs=TRUSTED_PROXY_CIDRS,  # noqa: F405
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/$"]  # el healthcheck interno va por HTTP
# HSTS corto por defecto: un error de HTTPS se corrige en una hora, no en un año. Súbelo por
# etapas cuando el HTTPS esté estable (docs/guia-instalacion.md). Sin preload.
SECURE_HSTS_SECONDS = env.int("HSTS_SECONDS", default=3600)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("HSTS_INCLUDE_SUBDOMAINS", default=False)  # noqa: F405
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Prefijo __Host-: el navegador solo lo acepta con Secure, Path=/ y sin Domain (así se configura).
SESSION_COOKIE_NAME = "__Host-sessionid"
CSRF_COOKIE_NAME = "__Host-csrftoken"
