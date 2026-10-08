"""URLconf de pruebas: las rutas reales de auth más una vista de prueba por cada acción.

Cada vista `probe/<accion>/` acepta GET, POST, PUT, PATCH y DELETE y exige únicamente su
acción, para ejercitar la matriz rol × acción × método. `probe/sin-permisos/` no declara
permisos a propósito: debe quedar denegada por defecto.
"""

from django.urls import include, path
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.accounts.permissions import Accion, requiere

METODOS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def ruta_de(accion: Accion) -> str:
    return f"api/v1/probe/{accion.value.lower()}/"


def vista_para(accion: Accion):
    @api_view(METODOS)
    @permission_classes([requiere(accion)])
    def vista(request):
        return Response({"ok": True})

    vista.__name__ = f"probe_{accion.value.lower()}"
    return vista


@api_view(METODOS)
def sin_permisos(request):
    return Response({"ok": True})


urlpatterns = [
    *[path(ruta_de(accion), vista_para(accion)) for accion in Accion],
    path("api/v1/probe/sin-permisos/", sin_permisos),
    path("api/v1/auth/", include("apps.accounts.urls")),
]
