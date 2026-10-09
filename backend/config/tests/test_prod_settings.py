import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.guards import DEFAULT_TRUSTED_PROXY_CIDRS, validate_production

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = Path("/opt/test/.env.example")  # montado por el servicio `test` del compose
GOOD_SECRET = "k" * 64

GOOD_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    "DJANGO_SECRET_KEY": GOOD_SECRET,
    "DJANGO_ALLOWED_HOSTS": "metrologia.example.com",
    "DB_NAME": "x",
    "DB_USER": "app",
    "DB_PASSWORD": "una-clave-real-de-base-de-datos",
    "TRUSTED_PROXY_CIDRS": "172.28.0.0/24",
}


def load_env_example() -> dict[str, str]:
    assert ENV_EXAMPLE.exists(), "El servicio test debe montar .env.example en /opt/test/.env.example"
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
        "trusted_proxy_cidrs": ["172.28.0.0/24"],
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


@pytest.mark.parametrize(
    ("cidrs", "fragmento"),
    [
        (DEFAULT_TRUSTED_PROXY_CIDRS, "valor por defecto"),
        (list(reversed(DEFAULT_TRUSTED_PROXY_CIDRS)), "valor por defecto"),
        (["10.0.0.0/8"], "demasiado amplio"),
        (["172.16.0.0/12"], "demasiado amplio"),
        (["172.28.0.0/24", "10.0.0.0/8"], "10.0.0.0/8"),
        (["0.0.0.0/0"], "demasiado amplio"),
        (["::/0"], "demasiado amplio"),
        (["2001:db8::/32"], "demasiado amplio"),
        (["no-es-un-cidr"], "inválido"),
        ([], "vacío"),
        (["", "  "], "vacío"),
    ],
)
def test_validator_rejects_broad_empty_or_invalid_trusted_proxies(cidrs, fragmento):
    with pytest.raises(ImproperlyConfigured, match="TRUSTED_PROXY_CIDRS") as error:
        validate_production(**good_args(trusted_proxy_cidrs=cidrs))

    assert fragmento in str(error.value)


@pytest.mark.parametrize(
    "cidrs",
    [
        ["172.28.0.0/24"],
        ["172.18.0.0/16"],
        ["127.0.0.1/32"],
        ["fd00::/64"],
        ["172.28.0.0/24", "::1/128"],
    ],
)
def test_validator_accepts_narrow_trusted_proxies(cidrs):
    validate_production(**good_args(trusted_proxy_cidrs=cidrs))


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
        ({"TRUSTED_PROXY_CIDRS": "10.0.0.0/8,172.16.0.0/12"}, "TRUSTED_PROXY_CIDRS"),
        ({"TRUSTED_PROXY_CIDRS": ""}, "TRUSTED_PROXY_CIDRS"),
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
# El proxy (Caddy) está dentro de TRUSTED_PROXY_CIDRS (172.28.0.0/24).
client = Client(
    HTTP_HOST="metrologia.example.com", REMOTE_ADDR="172.28.0.5", HTTP_X_FORWARDED_PROTO="https"
)
print(client.get("/api/v1/schema/").status_code)
internal = Client(HTTP_HOST="metrologia.example.com")  # sin cabecera de proxy: healthcheck interno
print(internal.get("/api/v1/health/").status_code)
print(internal.get("/api/v1/schema/").status_code)
# Un cliente fuera de la red de confianza no puede declarar HTTPS con la cabecera.
forged = Client(
    HTTP_HOST="metrologia.example.com", REMOTE_ADDR="203.0.113.9", HTTP_X_FORWARDED_PROTO="https"
)
print(forged.get("/api/v1/schema/").status_code)
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

    schema_https, health_http, schema_http, schema_forged = result.stdout.split()
    assert schema_https == "404"
    assert health_http in {"200", "503"}  # nunca 301: el healthcheck no sigue redirecciones
    assert schema_http == "301"
    assert schema_forged == "301"  # cabecera falsificada desde fuera de los proxies de confianza


def test_prod_refuses_to_start_when_trusted_proxies_are_left_at_the_default():
    # Sin la variable, base.py usa el valor por defecto de desarrollo (todos los rangos privados).
    env = {k: v for k, v in GOOD_ENV.items() if k != "TRUSTED_PROXY_CIDRS"}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import config.settings.prod"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "valor por defecto" in result.stderr


def test_the_env_example_value_is_rejected_by_prod():
    ejemplo = load_env_example()["TRUSTED_PROXY_CIDRS"]

    result = import_prod(TRUSTED_PROXY_CIDRS=ejemplo)

    assert result.returncode != 0
    assert "TRUSTED_PROXY_CIDRS" in result.stderr


def test_a_docker_subnet_is_accepted_by_prod():
    assert import_prod(TRUSTED_PROXY_CIDRS="172.28.0.0/24").returncode == 0
