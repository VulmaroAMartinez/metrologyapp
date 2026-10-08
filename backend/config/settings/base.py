"""Configuración común. Todo valor sensible sale de variables de entorno."""

from pathlib import Path

import environ

from .guards import DEFAULT_TRUSTED_PROXY_CIDRS

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# Sin valor por defecto: si falta, Django no arranca.
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# Sin django.contrib.admin en la v1 (ver docs/decisiones.md).
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "rest_framework",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
    "apps.audit",
]

# Debe estar definido antes de la primera migración.
AUTH_USER_MODEL = "accounts.Usuario"

# Solo Argon2 (Django usa su parametrización por defecto).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        "OPTIONS": {"user_attributes": ("nombre", "correo")},
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": env.int("PASSWORD_MIN_LENGTH", default=10)},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

MIDDLEWARE = [
    "apps.core.middleware.TrustedProxyHeadersMiddleware",  # primero: sanea X-Forwarded-*
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

# --- Sesión y CSRF --------------------------------------------------------------------------
# Sesión en base de datos: cerrar sesión la borra en el servidor. La ventana es deslizante
# (se renueva con cada petición), así que SESSION_IDLE_MINUTES es inactividad, no duración.
SESSION_IDLE_MINUTES = env.int("SESSION_IDLE_MINUTES", default=30)
SESSION_COOKIE_AGE = SESSION_IDLE_MINUTES * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# El token CSRF se entrega por /auth/csrf/; la cookie no necesita ser legible desde JS.
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
# CORS cerrado: no se instala django-cors-headers; el frontend usa el mismo origen (Caddy).

# --- Inicio de sesión: bloqueo y límite de tasa (valores provisionales, ver decisiones.md) ---
LOGIN_MAX_FAILED_ATTEMPTS = env.int("LOGIN_MAX_FAILED_ATTEMPTS", default=5)
LOGIN_LOCKOUT_MINUTES = env.int("LOGIN_LOCKOUT_MINUTES", default=15)
LOGIN_RATE_PER_IP_EMAIL = env.int("LOGIN_RATE_PER_IP_EMAIL", default=5)  # por minuto
LOGIN_RATE_PER_IP = env.int("LOGIN_RATE_PER_IP", default=20)  # por minuto

# Redes desde las que se aceptan X-Forwarded-* (el proxy Caddy). Por defecto, rangos privados:
# la API solo es alcanzable desde la red interna de Docker. En producción, acotar a su subred.
TRUSTED_PROXY_CIDRS = env.list("TRUSTED_PROXY_CIDRS", default=DEFAULT_TRUSTED_PROXY_CIDRS)

# Caché compartida entre workers (tabla django_cache). Nunca LocMem: cada worker tendría
# su propio contador y el límite de intentos se multiplicaría por el número de procesos.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
        "OPTIONS": {"MAX_ENTRIES": 1_000_000, "CULL_FREQUENCY": 3},
    }
}

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# El servidor usa el rol `app`; las migraciones y las pruebas, el rol `migrator`
# (se elige solo con DB_USER / DB_PASSWORD; ver README).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("DB_HOST", default="db"),
        "PORT": env("DB_PORT", default="5432"),
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "CONN_MAX_AGE": 60,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "es-mx"
# Momentos en UTC en la base de datos; zona local solo para presentación.
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True

REST_FRAMEWORK = {
    # Cuántos proxies hay delante (Caddy = 1) para leer la IP real en X-Forwarded-For.
    "NUM_PROXIES": env.int("API_NUM_PROXIES", default=1),
    # Cerrado por defecto: toda vista nueva exige sesión y contraseña vigente; las públicas
    # (health, csrf, login, logout) lo declaran explícitamente. Los roles llegan en el commit 7.
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.StrictSessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "apps.accounts.permissions.PasswordChangeNotRequired",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Control de instrumentos de metrología",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# El esquema OpenAPI solo se sirve cuando se habilita explícitamente (dev).
OPENAPI_SCHEMA_ENABLED = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    # El SQL con parámetros incluiría hashes de contraseña.
    "loggers": {"django.db.backends": {"level": "INFO"}},
}
