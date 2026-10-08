"""URLconf de pruebas: las rutas reales de auth más una vista con los permisos por defecto."""

from django.urls import include, path
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def probe(request):
    return Response({"ok": True})


urlpatterns = [
    path("api/v1/probe/", probe),
    path("api/v1/auth/", include("apps.accounts.urls")),
]
