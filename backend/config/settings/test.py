from .dev import *  # noqa: F403

# Hasher rápido solo para pruebas; la configuración real se verifica en
# apps/accounts/tests/test_password_security.py.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
