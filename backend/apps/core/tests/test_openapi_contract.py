"""El contrato OpenAPI generado: sobre de error común, esquema de autenticación y códigos.

`make schema` (parte de `make ci`) valida el esquema con `spectacular --validate --fail-on-warn`;
aquí se comprueba su contenido.
"""

import json

import pytest
import yaml
from django.core.management import call_command

ERROR = "#/components/schemas/Error"
# Públicos y sin cuerpo: no tienen errores propios que documentar.
SIN_ERRORES_DOCUMENTADOS = {"/api/v1/auth/csrf/", "/api/v1/auth/logout/"}


@pytest.fixture(scope="module")
def esquema(tmp_path_factory):
    destino = tmp_path_factory.mktemp("openapi") / "schema.yml"
    # Falla (SchemaGenerationError) ante cualquier advertencia o error de validación.
    call_command("spectacular", "--validate", "--fail-on-warn", "--file", str(destino))
    return yaml.safe_load(destino.read_text(encoding="utf-8"))


def operaciones(esquema):
    for ruta, metodos in esquema["paths"].items():
        for metodo, operacion in metodos.items():
            if metodo in {"get", "post", "put", "patch", "delete"}:
                yield ruta, metodo, operacion


def por_id(esquema):
    return {operacion["operationId"]: operacion for _, _, operacion in operaciones(esquema)}


def estados(operacion):
    return set(operacion["responses"])


def test_the_schema_validates_without_warnings(esquema):
    assert esquema["openapi"].startswith("3.")
    assert len(list(operaciones(esquema))) >= 15


def test_every_operation_has_a_unique_explicit_id(esquema):
    ids = [operacion["operationId"] for _, _, operacion in operaciones(esquema)]

    assert len(ids) == len(set(ids))
    assert "usuarios_listar" in ids
    assert "usuarios_obtener" in ids


def test_the_common_error_envelope_is_documented(esquema):
    error = esquema["components"]["schemas"]["Error"]

    assert set(error["properties"]) == {"codigo", "detalle", "campos"}
    assert set(error["required"]) == {"codigo", "detalle"}  # `campos` solo en errores de campo
    assert error["properties"]["campos"]["additionalProperties"]["type"] == "array"


def test_every_error_response_uses_the_common_envelope(esquema):
    for ruta, metodo, operacion in operaciones(esquema):
        if ruta.endswith("/health/"):
            continue  # solo `{status}`
        errores = {c: r for c, r in operacion["responses"].items() if not c.startswith("2")}
        if ruta in SIN_ERRORES_DOCUMENTADOS:
            continue
        assert errores, (ruta, metodo)
        for codigo, respuesta in errores.items():
            contenido = respuesta["content"]["application/json"]["schema"]
            assert contenido["$ref"] == ERROR, (ruta, metodo, codigo)


@pytest.mark.parametrize(
    ("operacion", "esperados"),
    [
        ("auth_login", {"200", "400", "401", "415", "429"}),
        ("auth_cambiar_contrasena", {"200", "400", "401", "403", "409", "415", "429", "503"}),
        ("usuarios_crear", {"201", "400", "401", "403", "404", "409", "415", "503"}),
        ("usuarios_editar", {"200", "400", "401", "403", "404", "409", "415", "503"}),
        ("usuarios_cambiar_rol", {"200", "400", "403", "404", "409", "503"}),
        ("usuarios_desactivar", {"200", "401", "403", "404", "409", "503"}),
        ("usuarios_restablecer_contrasena", {"200", "400", "403", "404", "409", "503"}),
        ("bitacora_eventos", {"200", "400", "401", "403"}),
    ],
)
def test_each_operation_documents_its_error_statuses(esquema, operacion, esperados):
    assert esperados <= estados(por_id(esquema)[operacion])


def test_the_page_size_and_the_cursor_filters_are_documented_with_their_limits(esquema):
    parametros = {p["name"]: p for p in por_id(esquema)["bitacora_eventos"]["parameters"]}

    assert parametros["tamano"]["schema"]["minimum"] == 1
    assert parametros["tamano"]["schema"]["maximum"] == 100
    assert {"desde", "hasta", "usuario", "instrumento_id", "tipo", "cursor"} <= set(parametros)


def test_session_cookie_authentication_is_documented(esquema):
    esquemas = esquema["components"]["securitySchemes"]

    assert esquemas["cookieAuth"]["type"] == "apiKey"
    assert esquemas["cookieAuth"]["in"] == "cookie"
    protegida = por_id(esquema)["usuarios_listar"]
    assert {"cookieAuth": []} in protegida["security"]


def test_the_error_codes_that_were_removed_do_not_appear_anywhere(esquema):
    texto = json.dumps(esquema)

    for retirado in ("contrasena_actual_incorrecta", "contrasena_repetida", "contrasena_invalida"):
        assert retirado not in texto


def test_passwords_are_write_only_in_the_contract(esquema):
    campos = esquema["components"]["schemas"]
    for nombre in ("CrearUsuario", "RestablecerContrasena", "CambiarContrasena", "Login"):
        escritura = campos[nombre]["properties"]
        contrasenas = [p for n, p in escritura.items() if n.startswith("contrasena")]
        assert contrasenas, nombre
        assert all(p.get("writeOnly") for p in contrasenas), nombre
    respuesta = json.dumps(campos["UsuarioAdmin"]) + json.dumps(campos["UsuarioActual"])
    assert "contrasena" not in respuesta
    assert "password" not in respuesta
