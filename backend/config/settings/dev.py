from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver", *ALLOWED_HOSTS]  # noqa: F405
OPENAPI_SCHEMA_ENABLED = True  # lo consume `make api-client`
