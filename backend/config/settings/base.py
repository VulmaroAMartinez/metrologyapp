"""Configuración común. Todo valor sensible sale de variables de entorno."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# Sin valor por defecto: si falta, Django no arranca.
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "rest_framework",
    "drf_spectacular",
    "apps.core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

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

# Cuántos proxies hay delante (Caddy = 1) para leer la IP real en X-Forwarded-For.
NUM_PROXIES = env.int("API_NUM_PROXIES", default=1)

REST_FRAMEWORK = {
    # Autenticación y permisos reales llegan con el modelo de usuario (commits 4-7).
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
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
}
