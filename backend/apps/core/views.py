import logging

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import Http404
from drf_spectacular.views import SpectacularAPIView
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request):
    """Solo el estado: ni versiones ni detalles internos."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception("Health check: base de datos no disponible")
        return Response({"status": "error"}, status=503)
    return Response({"status": "ok"})


_schema_view = SpectacularAPIView.as_view(permission_classes=[AllowAny], authentication_classes=[])


def schema(request):
    """Esquema OpenAPI; se comporta como inexistente cuando está deshabilitado (prod)."""
    if not settings.OPENAPI_SCHEMA_ENABLED:
        raise Http404
    return _schema_view(request)
