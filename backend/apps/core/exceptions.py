"""Forma estándar de los errores de la API: {"codigo": ..., "detalle": ..., "campos": {...}}.

- `codigo` y `detalle` (texto legible) siempre están.
- `campos` ({nombre_campo: [mensajes]}) solo cuando el error es de campo; permite al frontend
  pintar cada mensaje junto a su campo. Es la forma de TODO `datos_invalidos`.

Los errores que ya traen su propio `codigo` (csrf_invalido, los de ErrorDeUsuario...) se
respetan; el resto se normaliza por código HTTP.
"""

from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

CODIGO_POR_ESTADO = {
    400: "solicitud_invalida",
    401: "no_autenticado",
    403: "permiso_denegado",
    404: "no_encontrado",
    405: "metodo_no_permitido",
    406: "no_aceptable",
    415: "tipo_no_soportado",
    429: "demasiadas_solicitudes",
}
DETALLE_DE_CAMPOS = "Revisa los campos indicados."
SIN_CAMPO = "non_field_errors"


def _mensajes(valor) -> list[str]:
    if isinstance(valor, list | tuple):
        return [m for item in valor for m in _mensajes(item)]
    if isinstance(valor, dict):
        return [m for item in valor.values() for m in _mensajes(item)]
    return [str(valor)]


def sobre_de_datos_invalidos(datos) -> dict:
    """Convierte el detalle de un ValidationError de DRF en el sobre estándar."""
    if not isinstance(datos, dict):
        return {"codigo": "datos_invalidos", "detalle": " ".join(_mensajes(datos))}
    generales = _mensajes(datos.get(SIN_CAMPO, []))
    campos = {nombre: _mensajes(valor) for nombre, valor in datos.items() if nombre != SIN_CAMPO}
    sobre = {"codigo": "datos_invalidos", "detalle": " ".join(generales) or DETALLE_DE_CAMPOS}
    if campos:
        sobre["campos"] = campos
    return sobre


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    datos = response.data
    if isinstance(exc, ValidationError):
        response.data = sobre_de_datos_invalidos(datos)
    elif isinstance(datos, dict) and "codigo" in datos:
        pass  # el error ya trae su código y detalle
    else:
        detalle = datos.get("detail", datos) if isinstance(datos, dict) else datos
        response.data = {
            "codigo": CODIGO_POR_ESTADO.get(response.status_code, "error"),
            "detalle": str(detalle) if not isinstance(detalle, list | dict) else detalle,
        }
    return response
