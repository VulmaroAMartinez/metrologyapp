"""Toda prueba contra el stack real que cuente intentos del límite de tasa (que mencione 429)
debe alinear su ventana: si no, falla una vez de cada tantas corridas, cuando el cambio de minuto
cae a mitad de la prueba."""

import ast
from pathlib import Path

AQUI = Path(__file__).parent
AYUDANTE = "esperar_si_la_ventana_esta_por_terminar"


def pruebas_que_cuentan_429():
    for archivo in sorted(AQUI.glob("test_*.py")):
        if archivo.name == Path(__file__).name:
            continue
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and nodo.name.startswith("test_"):
                menciona_429 = any(
                    isinstance(n, ast.Constant) and n.value == 429 for n in ast.walk(nodo)
                )
                if menciona_429:
                    yield archivo.name, nodo


def test_there_are_rate_limit_tests_to_protect():
    assert len(list(pruebas_que_cuentan_429())) >= 3


def test_every_proxy_test_that_counts_429_aligns_its_window():
    sin_alinear = [
        f"{archivo}::{nodo.name}"
        for archivo, nodo in pruebas_que_cuentan_429()
        if not any(
            isinstance(n, ast.Call) and getattr(n.func, "id", "") == AYUDANTE
            for n in ast.walk(nodo)
        )
    ]

    assert not sin_alinear, f"no alinean la ventana del límite de tasa: {sin_alinear}"
