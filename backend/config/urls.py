from django.urls import include, path

urlpatterns = [
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/usuarios/", include("apps.accounts.urls_usuarios")),
    path("api/v1/bitacora/", include("apps.audit.urls")),
    path("api/v1/", include("apps.core.urls")),
]
