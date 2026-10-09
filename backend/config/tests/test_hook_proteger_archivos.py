"""Hook PreToolUse `.claude/hooks/proteger-archivos.sh`: protege DEFINICION.md, .env* y secrets/.

Recibe por stdin el JSON de la herramienta (`tool_input.file_path`) y bloquea con el código 2.
Debe funcionar con rutas de Windows (`C:\\...`, con `\\`) y de POSIX, ignorar mayúsculas y `..`,
y FALLAR CERRADO: sin jq, con una entrada ilegible o sin ruta, bloquea en vez de dejar pasar.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path("/opt/test/hooks/proteger-archivos.sh")
BASH = "/bin/bash"
BLOQUEADO = 2

PROTEGIDAS = [
    # DEFINICION.md con distintos formatos de ruta
    "/proj/docs/DEFINICION.md",
    "C:\\proj\\docs\\DEFINICION.md",
    "C:/proj/docs/DEFINICION.md",
    "C:\\Users\\vulma\\Downloads\\metrologyapp\\docs\\DEFINICION.md",
    "c:\\proj\\DOCS\\definicion.md",
    "/proj/docs/./DEFINICION.md",
    "/proj/docs/x/../DEFINICION.md",
    "C:\\proj\\docs\\x\\..\\DEFINICION.md",
    # .env y todas sus variantes (menos la plantilla .env.example)
    "/proj/.env",
    "C:\\proj\\.env",
    "C:\\proj\\.ENV",
    "/proj/.env.local",
    "/proj/.env.production",
    "C:\\proj\\backend\\.env.local",
    # carpeta secrets/
    "/proj/secrets/clave.txt",
    "/proj/secrets/a/b.pem",
    "C:\\proj\\secrets\\clave.txt",
    "C:\\proj\\Secrets\\clave.txt",
]

PERMITIDAS = [
    "/proj/backend/apps/accounts/services.py",
    "C:\\proj\\backend\\apps\\accounts\\services.py",
    "C:\\proj\\docs\\decisiones.md",
    "/proj/docs/definicion-notas.md",
    "/proj/.env.example",  # plantilla sin secretos: se versiona y se edita
    "C:\\proj\\.env.example",
    "/proj/environment.py",
    "/proj/backend/secretsmanager.py",  # el segmento debe ser exactamente `secrets`
]


def entrada(ruta: str, clave: str = "file_path") -> str:
    return json.dumps({"tool_name": "Edit", "tool_input": {clave: ruta}})


def ejecutar(texto: str, *, path: str | None = None) -> subprocess.CompletedProcess:
    jq = shutil.which("jq")
    assert jq, "la imagen de pruebas debe incluir jq (Dockerfile, etapa test)"
    return subprocess.run(  # noqa: S603
        [BASH, str(HOOK)],
        input=texto,
        capture_output=True,
        text=True,
        env={"PATH": path if path is not None else str(Path(jq).parent) + ":/usr/bin:/bin"},
        check=False,
    )


@pytest.mark.parametrize("ruta", PROTEGIDAS)
def test_protected_paths_are_blocked(ruta):
    resultado = ejecutar(entrada(ruta))

    assert resultado.returncode == BLOQUEADO, ruta
    assert "protegido" in resultado.stderr.lower()


@pytest.mark.parametrize("ruta", PERMITIDAS)
def test_other_paths_are_allowed(ruta):
    resultado = ejecutar(entrada(ruta))

    assert resultado.returncode == 0, f"{ruta}: {resultado.stderr}"


def test_a_notebook_path_is_checked_too():
    resultado = ejecutar(entrada("C:\\proj\\.env.local", clave="notebook_path"))

    assert resultado.returncode == BLOQUEADO


def test_it_blocks_when_jq_is_missing(tmp_path):
    resultado = ejecutar(entrada("/proj/backend/apps/x.py"), path=str(tmp_path))

    assert resultado.returncode == BLOQUEADO
    assert "jq" in resultado.stderr


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "esto no es json",
        "{",
        json.dumps({"tool_name": "Edit", "tool_input": {}}),
        json.dumps({"tool_name": "Edit"}),
        json.dumps({"tool_name": "Edit", "tool_input": {"file_path": ""}}),
        json.dumps({"tool_name": "Edit", "tool_input": {"file_path": 7}}),
    ],
)
def test_it_blocks_when_the_input_has_no_readable_path(texto):
    resultado = ejecutar(texto)

    assert resultado.returncode == BLOQUEADO, texto


def test_a_jq_that_prints_crlf_does_not_defeat_the_match(tmp_path):
    """jq.exe de Windows termina las líneas con \\r\\n: ese \\r no puede esconder el nombre."""
    real = shutil.which("jq")
    shim = tmp_path / "jq"
    shim.write_text(f"#!/bin/bash\n{real} \"$@\" | sed 's/$/\\r/'\n", encoding="utf-8")
    shim.chmod(0o755)

    resultado = ejecutar(entrada("C:\\proj\\docs\\DEFINICION.md"), path=f"{tmp_path}:/usr/bin:/bin")

    assert resultado.returncode == BLOQUEADO
