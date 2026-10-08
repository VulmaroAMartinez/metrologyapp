"""Forma estándar de los errores de la API: {"codigo": ..., "detalle": ...}.

Los errores que ya traen su propio `codigo` (debe_cambiar_contrasena, csrf_invalido...) se
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


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    datos = response.data
    if isinstance(exc, ValidationError):
        response.data = {"codigo": "datos_invalidos", "detalle": datos}
    elif isinstance(datos, dict) and "codigo" in datos:
        pass  # el error ya trae su código y detalle
    else:
        detalle = datos.get("detail", datos) if isinstance(datos, dict) else datos
        response.data = {
            "codigo": CODIGO_POR_ESTADO.get(response.status_code, "error"),
            "detalle": str(detalle) if not isinstance(detalle, list | dict) else detalle,
        }
    return response
