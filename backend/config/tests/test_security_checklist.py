"""La lista de verificación de seguridad no puede citar pruebas ni comandos que no existen."""

import re
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
# Montados por el servicio `test` del compose (fuera de /app para no ensuciar el repo).
CHECKLIST = Path("/opt/test/docs/seguridad-checklist.md")
MAKEFILE = Path("/opt/test/Makefile")

REFERENCIA = re.compile(r"`((?:apps|config|proxytests)/[\w/]+\.py)::(\w+)`")
COMANDO_MAKE = re.compile(r"`make ([a-z-]+)`")


@pytest.fixture(scope="module")
def texto():
    assert CHECKLIST.exists(), "El servicio test debe montar docs/ en /opt/test/docs"
    return CHECKLIST.read_text(encoding="utf-8")


def test_the_checklist_cites_a_meaningful_number_of_tests(texto):
    referencias = REFERENCIA.findall(texto)

    assert len(referencias) >= 80
    assert len(set(referencias)) == len(referencias), "hay pruebas citadas dos veces"


def test_every_cited_test_exists(texto):
    inexistentes = []
    for archivo, prueba in REFERENCIA.findall(texto):
        ruta = BACKEND_DIR / archivo
        if not ruta.exists():
            inexistentes.append(f"{archivo} (no existe el archivo)")
        elif not re.search(rf"^(?:async )?def {prueba}\(", ruta.read_text(encoding="utf-8"), re.M):
            inexistentes.append(f"{archivo}::{prueba}")

    assert not inexistentes, "La lista cita pruebas inexistentes:\n" + "\n".join(inexistentes)


def test_every_cited_make_target_exists(texto):
    assert MAKEFILE.exists(), "El servicio test debe montar el Makefile en /opt/test/Makefile"
    objetivos = set(re.findall(r"^([a-z-]+):", MAKEFILE.read_text(encoding="utf-8"), re.M))

    citados = set(COMANDO_MAKE.findall(texto))

    assert citados, "la lista debería citar comandos make"
    assert citados <= objetivos, f"objetivos make inexistentes: {sorted(citados - objetivos)}"


def test_every_control_family_of_the_sprint_is_covered(texto):
    for tema in (
        "Contraseñas",
        "Bloqueo",
        "Límite de tasa",
        "IP real",
        "Sesión",
        "CSRF",
        "CORS",
        "Cabeceras",
        "Permisos",
        "Bitácora",
        "contenedores",
        "Secretos",
    ):
        assert tema.lower() in texto.lower(), tema
