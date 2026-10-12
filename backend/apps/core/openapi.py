"""Contrato OpenAPI: el sobre de error común y las respuestas de error reutilizables.

El esquema se genera con drf-spectacular (`/api/v1/schema/` en desarrollo, `make schema` valida
que no haya advertencias). Todo error de la API tiene la forma `Error`.
"""

from drf_spectacular.utils import OpenApiResponse
from rest_framework import serializers


class Error(serializers.Serializer):
    """Sobre de todos los errores: `codigo` estable para programas, `detalle` legible y, solo si
    el error es de campo, `campos` ({nombre_campo: [mensajes]})."""

    codigo = serializers.CharField(help_text="Código estable, p. ej. `datos_invalidos`.")
    detalle = serializers.CharField(help_text="Mensaje legible en español.")
    campos = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        help_text="Mensajes por campo; solo en errores de campo (`datos_invalidos`...).",
    )


def _error(descripcion: str) -> OpenApiResponse:
    return OpenApiResponse(response=Error, description=descripcion)


DATOS_INVALIDOS = _error("`datos_invalidos`: el cuerpo o los parámetros no son válidos.")
NO_AUTENTICADO = _error("`no_autenticado`: falta la sesión o venció.")
PERMISO_DENEGADO = _error("`permiso_denegado` o `csrf_invalido`: el rol no tiene la acción.")
NO_ENCONTRADO = _error("`no_encontrado`: el recurso no existe.")
CONFLICTO = _error("Conflicto con el estado actual (código propio en `codigo`).")
DEMASIADAS_SOLICITUDES = _error(
    "`demasiados_intentos`: límite de tasa; la cabecera `Retry-After` indica cuándo reintentar."
)
REINTENTAR = _error("`reintentar`: el sistema está ocupado; la operación no se aplicó.")
TIPO_NO_SOPORTADO = _error("`tipo_no_soportado`: la API solo acepta cuerpos JSON.")
