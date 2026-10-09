"""Despliegue: .gitignore, proxytest.sh, compose, Makefile y .env.example.

Los monta el servicio `test` del compose en /opt/test (ver deploy/docker-compose.yml).
"""

import re
from pathlib import Path

import pytest
import yaml

from config.settings.guards import DEFAULT_TRUSTED_PROXY_CIDRS

RAIZ = Path("/opt/test")
COMPOSE = RAIZ / "deploy" / "docker-compose.yml"
PROXYTEST_SH = RAIZ / "deploy" / "proxytest.sh"
GITIGNORE = RAIZ / ".gitignore"
MAKEFILE = RAIZ / "Makefile"
ENV_EJEMPLO = RAIZ / ".env.example"
MASQUERADE = "com.docker.network.bridge.enable_ip_masquerade"


def lineas_activas(ruta: Path) -> list[str]:
    return [
        linea
        for linea in ruta.read_text(encoding="utf-8").splitlines()
        if linea.strip() and not linea.lstrip().startswith("#")
    ]


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# --- .gitignore y proxytest.sh ------------------------------------------------------------


def test_the_proxytest_secrets_file_is_ignored_by_git():
    ignoradas = {linea.strip() for linea in lineas_activas(GITIGNORE)}

    assert ".proxytest.env" in ignoradas


def test_proxytest_never_puts_the_admin_password_on_a_command_line():
    """`-e NOMBRE=valor` deja la clave en la lista de procesos del host; `-e NOMBRE` la toma del
    entorno del propio comando sin mostrarla."""
    guion = PROXYTEST_SH.read_text(encoding="utf-8")

    con_valor = re.findall(r"-e\s+\w*(?:CONTRASENA|CLAVE|PASSWORD)\w*=\S+", guion)

    assert not con_valor, f"la clave va en la línea de comandos: {con_valor}"
    assert re.search(r"-e\s+CREAR_ADMIN_CONTRASENA(?=\s)", guion)
    assert re.search(r"-e\s+PROXYTEST_ADMIN_CONTRASENA(?=\s)", guion)


# --- una sola fuente para los proxies de confianza por defecto --------------------------------


def test_the_default_proxy_list_lives_only_in_guards(compose):
    texto_compose = COMPOSE.read_text(encoding="utf-8")
    ejemplo = "\n".join(lineas_activas(ENV_EJEMPLO))

    for cidr in DEFAULT_TRUSTED_PROXY_CIDRS:
        assert cidr not in texto_compose, f"{cidr} está duplicado en el compose"
        assert cidr not in ejemplo, f"{cidr} está duplicado en .env.example"


def test_the_env_example_documents_but_does_not_set_trusted_proxy_cidrs():
    activas = [linea for linea in lineas_activas(ENV_EJEMPLO) if "TRUSTED_PROXY_CIDRS" in linea]
    documentada = "TRUSTED_PROXY_CIDRS" in ENV_EJEMPLO.read_text(encoding="utf-8")

    assert not activas
    assert documentada


# --- redes del compose: la base de datos sin salida; solo api sale ---------------------------


def redes_de(servicio: dict) -> list[str]:
    redes = servicio.get("networks", [])
    return list(redes) if isinstance(redes, list | dict) else []


def tiene_salida(red: dict) -> bool:
    """Una red tiene salida a internet si no es `internal` y conserva el enmascaramiento (NAT)."""
    if red.get("internal") is True:
        return False
    return (red.get("driver_opts") or {}).get(MASQUERADE) != "false"


def test_every_network_of_the_database_is_internal(compose):
    redes = compose["networks"]

    for nombre in redes_de(compose["services"]["db"]):
        assert redes[nombre].get("internal") is True, f"la red {nombre} de db no es internal"


def test_only_api_can_reach_the_outside(compose):
    redes = compose["networks"]

    con_salida = {
        nombre
        for nombre, servicio in compose["services"].items()
        if any(tiene_salida(redes[red]) for red in redes_de(servicio))
    }

    assert con_salida == {"api"}


def test_the_proxy_keeps_a_published_port_without_egress(compose):
    redes = compose["networks"]
    proxy = compose["services"]["proxy"]
    publicadas = [red for red in redes_de(proxy) if redes[red].get("internal") is not True]

    assert proxy["ports"]  # el puerto publicado exige una red que no sea internal
    assert publicadas
    assert all(not tiene_salida(redes[red]) for red in publicadas)
    publican = {n for n, s in compose["services"].items() if s.get("ports")}
    assert publican == {"proxy"}


def test_every_service_still_reaches_the_database_it_needs(compose):
    for nombre in ("api", "migrate", "test"):
        comparte = set(redes_de(compose["services"][nombre])) & set(
            redes_de(compose["services"]["db"])
        )
        assert comparte, f"{nombre} no comparte red con db"


# --- Makefile bajo Git Bash ---------------------------------------------------------------


def test_the_makefile_disables_msys_path_conversion():
    """Git Bash convierte `C:/ruta:/destino` en una lista de rutas de Windows y Docker crea una
    carpeta basura (`backend;C`) en el repositorio."""
    texto = MAKEFILE.read_text(encoding="utf-8")

    assert re.search(r"^export MSYS_NO_PATHCONV\s*:?=\s*1\s*$", texto, re.MULTILINE)


def test_the_makefile_has_no_double_slash_path_workarounds():
    texto = MAKEFILE.read_text(encoding="utf-8")

    assert not re.search(r"(-w|=|\s)//[a-z]", texto), "quedan rutas con // (solución a medias)"
