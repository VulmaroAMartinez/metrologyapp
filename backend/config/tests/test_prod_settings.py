import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.guards import validate_production

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = Path("/app/.env.example")  # montado por el servicio `test` del compose
GOOD_SECRET = "k" * 64

GOOD_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    "DJANGO_SECRET_KEY": GOOD_SECRET,
    "DJANGO_ALLOWED_HOSTS": "metrologia.example.com",
    "DB_NAME": "x",
    "DB_USER": "app",
    "DB_PASSWORD": "una-clave-real-de-base-de-datos",
}


def load_env_example() -> dict[str, str]:
    assert ENV_EXAMPLE.exists(), "El servicio test debe montar .env.example en /app/.env.example"
    pairs = (
        line.split("=", 1)
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )
    return {key: value for key, value in pairs}


def import_prod(**overrides: str) -> subprocess.CompletedProcess:
    env = {**GOOD_ENV, **overrides}
    env = {key: value for key, value in env.items() if value is not None}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import config.settings.prod"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def good_args(**overrides):
    return {
        "secret_key": GOOD_SECRET,
        "db_password": "clave-real",
        "allowed_hosts": ["a.example.com"],
        "debug": False,
        **overrides,
    }


def test_valid_configuration_is_accepted():
    validate_production(**good_args())
    assert import_prod().returncode == 0


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"debug": True}, "DJANGO_DEBUG"),
        ({"allowed_hosts": []}, "ALLOWED_HOSTS"),
        ({"secret_key": "EJEMPLO-lo-que-sea"}, "SECRET_KEY"),
        ({"secret_key": "corta"}, "SECRET_KEY"),
        ({"db_password": "EJEMPLO-cambia-app"}, "DB_PASSWORD"),
    ],
)
def test_validator_rejects_insecure_values(override, expected):
    with pytest.raises(ImproperlyConfigured, match=expected):
        validate_production(**good_args(**override))


def test_validator_reports_every_problem():
    with pytest.raises(ImproperlyConfigured) as error:
        validate_production(**good_args(debug=True, allowed_hosts=[]))
    assert "DJANGO_DEBUG" in str(error.value) and "ALLOWED_HOSTS" in str(error.value)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"DJANGO_DEBUG": "true"}, "DJANGO_DEBUG"),
        ({"DJANGO_ALLOWED_HOSTS": ""}, "ALLOWED_HOSTS"),
        ({"DJANGO_SECRET_KEY": "EJEMPLO-cambia-esta-clave-larga-y-aleatoria"}, "SECRET_KEY"),
        ({"DB_PASSWORD": "EJEMPLO-cambia-app"}, "DB_PASSWORD"),
    ],
)
def test_prod_refuses_to_start(overrides, expected):
    result = import_prod(**overrides)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert expected in result.stderr


def test_prod_refuses_to_start_without_secret_key():
    env = {k: v for k, v in GOOD_ENV.items() if k != "DJANGO_SECRET_KEY"}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import config.settings.prod"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


def test_every_env_example_secret_is_rejected_by_prod():
    example = load_env_example()
    secrets = {
        "DJANGO_SECRET_KEY": example["DJANGO_SECRET_KEY"],
        "DB_PASSWORD": example["APP_PASSWORD"],
    }

    for variable, value in secrets.items():
        result = import_prod(**{variable: value})
        assert result.returncode != 0, f"prod aceptó el valor de ejemplo de {variable}"

    # Ningún secreto de la plantilla puede carecer de la marca que prod rechaza.
    for name in ("POSTGRES_PASSWORD", "MIGRATOR_PASSWORD", "APP_PASSWORD", "DJANGO_SECRET_KEY"):
        assert example[name].startswith("EJEMPLO"), name


PROD_REQUESTS = """
import django
django.setup()
from django.test import Client
client = Client(HTTP_HOST="metrologia.example.com", HTTP_X_FORWARDED_PROTO="https")
print(client.get("/api/v1/schema/").status_code)
internal = Client(HTTP_HOST="metrologia.example.com")  # sin cabecera de proxy: healthcheck interno
print(internal.get("/api/v1/health/").status_code)
print(internal.get("/api/v1/schema/").status_code)
"""


def test_prod_hides_schema_and_exempts_health_from_https_redirect():
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", PROD_REQUESTS],
        cwd=BACKEND_DIR,
        env=GOOD_ENV,
        capture_output=True,
        text=True,
        check=False,
    )

    schema_https, health_http, schema_http = result.stdout.split()
    assert schema_https == "404"
    assert health_http in {"200", "503"}  # nunca 301: el healthcheck no sigue redirecciones
    assert schema_http == "301"
