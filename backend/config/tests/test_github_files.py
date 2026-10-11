"""Integración continua: el flujo de GitHub Actions es seguro y equivale a `make ci`.

Lo monta el servicio `test` del compose en /opt/test/.github.
"""

import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path("/opt/test")
CI = RAIZ / ".github" / "workflows" / "ci.yml"
DEPENDABOT = RAIZ / ".github" / "dependabot.yml"
MAKEFILE = RAIZ / "Makefile"
USO = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.M)


@pytest.fixture(scope="module")
def flujo():
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def test_every_action_is_pinned_to_a_full_commit_sha():
    acciones = USO.findall(CI.read_text(encoding="utf-8"))

    assert acciones
    sueltas = [a for a in acciones if not re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", a)]
    assert not sueltas, f"acciones sin fijar por SHA: {sueltas}"


def test_the_token_has_read_only_contents_permission_and_no_secrets_are_used(flujo):
    assert flujo["permissions"] == {"contents": "read"}
    assert all("permissions" not in trabajo for trabajo in flujo["jobs"].values())
    assert "secrets." not in CI.read_text(encoding="utf-8")


def test_it_runs_on_push_and_pull_request_to_main(flujo):
    disparadores = flujo.get("on", flujo.get(True))  # PyYAML lee la clave `on` como True

    assert disparadores["push"]["branches"] == ["main"]
    assert disparadores["pull_request"]["branches"] == ["main"]


def test_the_jobs_cover_every_make_ci_target():
    linea = re.search(r"^ci: \.env (.+?) ##", MAKEFILE.read_text(encoding="utf-8"), re.M)
    ejecutados = set(re.findall(r"run: make ([a-z-]+)", CI.read_text(encoding="utf-8")))

    assert linea
    faltan = set(linea.group(1).split()) - ejecutados
    assert not faltan, f"make ci incluye {sorted(faltan)} y el CI no"


def test_dependabot_watches_the_four_ecosystems():
    actualizaciones = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))["updates"]

    ecosistemas = {a["package-ecosystem"] for a in actualizaciones}
    assert {"pip", "npm", "docker", "github-actions"} <= ecosistemas
