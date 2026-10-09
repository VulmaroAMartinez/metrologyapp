"""Lectura de la bitácora: solo ADMIN (VER_BITACORA). Solo GET; consultar no genera evento."""

from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.utils.urls import replace_query_param

from apps.accounts.permissions import Accion, requiere
from apps.accounts.views import respuesta
from apps.core.openapi import DATOS_INVALIDOS, NO_AUTENTICADO, PERMISO_DENEGADO

from . import services
from .serializers import EventoSerializer, FiltrosBitacoraSerializer, interpretar_consulta

PaginaDeEventos = inline_serializer(
    "PaginaDeEventos",
    {
        "next": drf_serializers.URLField(allow_null=True, help_text="URL de la página siguiente."),
        "results": EventoSerializer(many=True),
    },
)


@extend_schema(
    operation_id="bitacora_eventos",
    tags=["bitacora"],
    summary="Eventos de la bitácora (solo ADMIN, paginación por cursor)",
    parameters=[
        FiltrosBitacoraSerializer,
        OpenApiParameter(
            "tipo", str, description="Tipo de evento; repetible o separado por comas."
        ),
    ],
    responses={
        200: PaginaDeEventos,
        400: DATOS_INVALIDOS,
        401: NO_AUTENTICADO,
        403: PERMISO_DENEGADO,
    },
)
@api_view(["GET"])
@permission_classes([requiere(Accion.VER_BITACORA)])
def eventos(request):
    filtros, texto_cursor, tamano = interpretar_consulta(request.query_params)
    cursor = None
    if texto_cursor:
        try:
            cursor = services.decodificar_cursor(texto_cursor)
        except services.CursorInvalidoError as error:
            raise ValidationError({"cursor": [str(error)]}) from error

    filas, siguiente = services.consultar_eventos(filtros, cursor, tamano)

    url_siguiente = None
    if siguiente:
        url_siguiente = replace_query_param(request.build_absolute_uri(), "cursor", siguiente)
    return respuesta({"next": url_siguiente, "results": EventoSerializer(filas, many=True).data})
