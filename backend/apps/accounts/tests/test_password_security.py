import logging
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.hashers import Argon2PasswordHasher
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings

from apps.accounts.models import Usuario
from config.settings import base

pytestmark = pytest.mark.django_db

BACKEND_DIR = Path(__file__).resolve().parents[3]
REAL_HASHERS = base.PASSWORD_HASHERS
SECRET = "Cl4ve-muy-secreta-9"


@pytest.fixture
def usuario():
    return Usuario(nombre="Guadalupe Hernández", correo="guadalupe.hernandez@empresa.test")


def error_codes(password, user=None):
    with pytest.raises(ValidationError) as error:
        validate_password(password, user)
    return {e.code for e in error.value.error_list}


# --- hasher -------------------------------------------------------------------------------


def test_real_configuration_uses_argon2_first():
    assert REAL_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"


def test_real_hasher_stores_argon2id_hash():
    with override_settings(PASSWORD_HASHERS=REAL_HASHERS):
        usuario = Usuario.objects.create_user("a@e.test", "A", "ADMIN", password=SECRET)

        assert usuario.password.startswith("argon2$argon2id$")
        assert usuario.check_password(SECRET)
        assert not usuario.check_password("otra-clave-distinta")
        assert Argon2PasswordHasher().algorithm == "argon2"


def test_prod_settings_use_argon2():
    env = {
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": "k" * 64,
        "DJANGO_ALLOWED_HOSTS": "metrologia.example.com",
        "DB_NAME": "x",
        "DB_USER": "app",
        "DB_PASSWORD": "una-clave-real",
        "TRUSTED_PROXY_CIDRS": "172.28.0.0/24",
    }
    code = "from django.conf import settings; print(settings.PASSWORD_HASHERS)"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stdout.strip() == str(REAL_HASHERS)


def test_test_settings_are_the_only_ones_with_a_fast_hasher():
    assert settings.PASSWORD_HASHERS == ["django.contrib.auth.hashers.MD5PasswordHasher"]
    assert "MD5" not in str(REAL_HASHERS)


# --- validadores --------------------------------------------------------------------------


def test_minimum_length_default_is_10():
    assert "password_too_short" in error_codes("Ab1-cdef9")  # 9 caracteres
    validate_password("Ab1-cdef98")  # 10 caracteres: pasa


def test_minimum_length_is_configurable_by_environment():
    env = {
        "DJANGO_SETTINGS_MODULE": "config.settings.dev",
        "DJANGO_SECRET_KEY": "x",
        "DB_NAME": "x",
        "DB_USER": "x",
        "DB_PASSWORD": "x",
        "PASSWORD_MIN_LENGTH": "14",
    }
    code = (
        "from django.conf import settings;"
        "print(settings.AUTH_PASSWORD_VALIDATORS[1]['OPTIONS']['min_length'])"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stdout.strip() == "14"


def test_common_passwords_are_rejected():
    assert "password_too_common" in error_codes("qwertyuiop")


def test_numeric_only_passwords_are_rejected():
    assert "password_entirely_numeric" in error_codes("48291746503")


@pytest.mark.parametrize("password", ["guadalupehernandez", "GUADALUPE.HERNANDEZ@empresa"])
def test_passwords_similar_to_name_or_email_are_rejected(usuario, password):
    assert "password_too_similar" in error_codes(password, usuario)


def test_strong_unrelated_password_is_accepted(usuario):
    validate_password("tornillo-azul-77-nube", usuario)


# --- la contraseña no se filtra -----------------------------------------------------------


def test_password_never_appears_in_repr_or_str():
    usuario = Usuario.objects.create_user("a@e.test", "A", "ADMIN", password=SECRET)

    for text in (repr(usuario), str(usuario), f"{usuario}", repr(Usuario.objects.all())):
        assert SECRET not in text
        assert usuario.password not in text


def test_password_and_hash_never_reach_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    connection.force_debug_cursor = True  # el SQL con parámetros se registraría aquí
    try:
        with override_settings(PASSWORD_HASHERS=REAL_HASHERS):
            usuario = Usuario.objects.create_user("a@e.test", "A", "ADMIN", password=SECRET)
            usuario.check_password(SECRET)
            usuario.refresh_from_db()
    finally:
        connection.force_debug_cursor = False

    assert logging.getLogger("django.db.backends").getEffectiveLevel() == logging.INFO
    assert SECRET not in caplog.text
    assert usuario.password not in caplog.text


def test_control_sql_logging_at_debug_would_leak_the_hash(caplog):
    """Prueba de que la anterior no es vacía: sin el nivel INFO del LOGGING, el hash sale."""
    connection.force_debug_cursor = True
    try:
        with caplog.at_level(logging.DEBUG, logger="django.db.backends"):
            usuario = Usuario.objects.create_user("b@e.test", "B", "ADMIN", password=SECRET)
    finally:
        connection.force_debug_cursor = False

    assert usuario.password in caplog.text
