"""Regla (apps/accounts/locks.py, docs/decisiones.md): toda ruta que escriba `rol` o `activo` de un
usuario toma el candado L0 de administradores.

Prueba estática con `ast` sobre el código de la aplicación (sin pruebas ni migraciones): falla si
OTRA función escribe esos campos, o si una de las permitidas deja de tomar L0. Mientras se tiene
L0 los administradores se leen sin candado de fila; eso solo es válido si nada más los cambia.
"""

import ast
from pathlib import Path

from apps.accounts import services

BACKEND = Path(__file__).resolve().parents[3]
CAMPOS = {"rol", "activo"}
ESCRITORES_DE_BD = {"update", "create", "update_or_create", "get_or_create", "Usuario", "model"}

# Funciones que escriben esos campos directamente, todas con L0 (`_bloquear`).
CON_CANDADO = {
    "apps/accounts/services/usuarios.py:cambiar_rol",
    "apps/accounts/services/usuarios.py:desactivar_usuario",
    "apps/accounts/services/usuarios.py:reactivar_usuario",
}
# Crea usuarios (con su rol) a través del manager de bajo nivel y también toma L0.
CREADOR = "apps/accounts/services/usuarios.py:crear_usuario"
# Bajo nivel: solo lo llama `crear_usuario` (se comprueba abajo).
DE_BAJO_NIVEL = {
    "apps/accounts/managers.py:UsuarioManager.create_user",
}
CREADOR_DE_BAJO_NIVEL = "apps/accounts/services/usuarios.py:_crear_en_bd"


def archivos_de_codigo():
    for ruta in sorted((BACKEND / "apps").rglob("*.py")):
        partes = set(ruta.relative_to(BACKEND).parts)
        if partes & {"tests", "migrations"}:
            continue
        yield ruta


def funciones(arbol):
    """(nombre calificado, nodo) de cada función, con la clase que la contiene."""
    pendientes = [(arbol, "")]
    while pendientes:
        nodo, prefijo = pendientes.pop()
        for hijo in ast.iter_child_nodes(nodo):
            if isinstance(hijo, ast.ClassDef):
                pendientes.append((hijo, f"{prefijo}{hijo.name}."))
            elif isinstance(hijo, ast.FunctionDef | ast.AsyncFunctionDef):
                yield f"{prefijo}{hijo.name}", hijo
                pendientes.append((hijo, f"{prefijo}{hijo.name}."))


def nombre_de_la_llamada(llamada):
    destino = llamada.func
    if isinstance(destino, ast.Attribute):
        return destino.attr
    return destino.id if isinstance(destino, ast.Name) else ""


def aplanar(objetivos):
    """Incluye los destinos de asignaciones múltiples: `a.rol, a.activo = x, y`."""
    for destino in objetivos:
        if isinstance(destino, ast.Tuple | ast.List):
            yield from aplanar(destino.elts)
        else:
            yield destino


def escribe_los_campos(funcion, campos=CAMPOS) -> bool:
    for nodo in ast.walk(funcion):
        if isinstance(nodo, ast.Assign | ast.AugAssign | ast.AnnAssign):
            objetivos = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
            if any(isinstance(t, ast.Attribute) and t.attr in campos for t in aplanar(objetivos)):
                return True
        if isinstance(nodo, ast.Call):
            nombre = nombre_de_la_llamada(nodo)
            if nombre in ESCRITORES_DE_BD and any(k.arg in campos for k in nodo.keywords):
                return True
            if nombre == "setattr" and len(nodo.args) > 1:
                campo = nodo.args[1]
                if isinstance(campo, ast.Constant) and campo.value in campos:
                    return True
            if nombre == "bulk_update" and any(
                isinstance(c, ast.Constant) and c.value in campos
                for argumento in nodo.args
                for c in ast.walk(argumento)
            ):
                return True
    return False


def toma_el_candado(funcion) -> bool:
    return any(
        isinstance(n, ast.Call) and nombre_de_la_llamada(n) == "_bloquear"
        for n in ast.walk(funcion)
    )


def escritores_de(campos):
    encontrados = {}
    for ruta in archivos_de_codigo():
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        relativa = ruta.relative_to(BACKEND).as_posix()
        for nombre, funcion in funciones(arbol):
            if escribe_los_campos(funcion, campos):
                encontrados[f"{relativa}:{nombre}"] = funcion
    return encontrados


def escritores():
    encontrados = {}
    for ruta in archivos_de_codigo():
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        relativa = ruta.relative_to(BACKEND).as_posix()
        for nombre, funcion in funciones(arbol):
            if escribe_los_campos(funcion):
                encontrados[f"{relativa}:{nombre}"] = funcion
    return encontrados


def test_only_the_known_functions_write_rol_or_activo():
    encontrados = set(escritores())

    esperados = CON_CANDADO | DE_BAJO_NIVEL
    assert encontrados == esperados, (
        "Una función nueva escribe `rol` o `activo`: debe tomar L0 (`_bloquear`) y agregarse a "
        f"esta prueba. Sobrantes o faltantes: {sorted(encontrados ^ esperados)}"
    )


def test_every_writer_takes_the_administrators_lock():
    por_nombre = {}
    for ruta in archivos_de_codigo():
        relativa = ruta.relative_to(BACKEND).as_posix()
        for nombre, funcion in funciones(ast.parse(ruta.read_text(encoding="utf-8"))):
            por_nombre[f"{relativa}:{nombre}"] = funcion

    sin_candado = [n for n in CON_CANDADO | {CREADOR} if not toma_el_candado(por_nombre[n])]

    assert not sin_candado, f"escriben rol/activo sin tomar L0: {sin_candado}"


def test_the_low_level_creator_is_only_called_from_crear_usuario():
    llamadores = set()
    for ruta in archivos_de_codigo():
        relativa = ruta.relative_to(BACKEND).as_posix()
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for nombre, funcion in funciones(arbol):
            if any(
                isinstance(n, ast.Call) and nombre_de_la_llamada(n) == "create_user"
                for n in ast.walk(funcion)
            ):
                llamadores.add(f"{relativa}:{nombre}")

    assert llamadores == {CREADOR_DE_BAJO_NIVEL}
    # ... y `_crear_en_bd` solo se usa como punto de inyección de `crear_usuario`.
    usuarios_py = (BACKEND / "apps/accounts/services/usuarios.py").read_text(encoding="utf-8")
    quienes = [
        n
        for n, f in funciones(ast.parse(usuarios_py))
        if "crear_usuario_en_bd" in {x.attr for x in ast.walk(f) if isinstance(x, ast.Attribute)}
    ]
    assert quienes == ["crear_usuario"]


def test_editar_usuario_cannot_be_used_to_write_rol_or_activo():
    """`editar_usuario` escribe con setattr sobre los campos que deja pasar `limpiar_campos`."""
    intento = {
        "nombre": "x",
        "correo": "a@b.co",
        "numero_nomina": None,
        "rol": "ADMIN",
        "activo": 0,
    }

    assert set(services.limpiar_campos(intento)) <= {"nombre", "correo", "numero_nomina"}


def test_only_deactivate_and_reset_bump_the_session_version_atomically_under_the_lock():
    """`sesion_version` sube solo ahí: UPDATE atómico (F), bajo L0 y la fila del objetivo."""
    escriben = {
        nombre
        for nombre, funcion in escritores_de(("sesion_version",)).items()
        if "tests" not in nombre
    }

    esperados = {
        "apps/accounts/services/usuarios.py:_invalidar_sesiones_anteriores",
    }
    assert escriben == esperados

    codigo = (BACKEND / "apps/accounts/services/usuarios.py").read_text(encoding="utf-8")
    arbol = ast.parse(codigo)
    por_nombre = {n: f for n, f in funciones(arbol)}
    ayudante = por_nombre["_invalidar_sesiones_anteriores"]
    assert "F('sesion_version')" in ast.unparse(ayudante)  # incremento atómico, no leer-y-guardar
    for nombre in ("desactivar_usuario", "restablecer_contrasena"):
        llamadas = {
            nombre_de_la_llamada(n) for n in ast.walk(por_nombre[nombre]) if isinstance(n, ast.Call)
        }
        assert "_invalidar_sesiones_anteriores" in llamadas, nombre
        assert toma_el_candado(por_nombre[nombre]), nombre
    otros = [
        n
        for n, f in funciones(arbol)
        if n
        not in {"desactivar_usuario", "restablecer_contrasena", "_invalidar_sesiones_anteriores"}
        and "_invalidar_sesiones_anteriores"
        in {nombre_de_la_llamada(c) for c in ast.walk(f) if isinstance(c, ast.Call)}
    ]
    assert otros == []
