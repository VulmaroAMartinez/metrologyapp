from django.urls import path

from . import views_usuarios as v

urlpatterns = [
    path("", v.usuarios, name="usuarios"),
    path("<int:pk>/", v.usuario, name="usuario"),
    path("<int:pk>/cambiar-rol/", v.cambiar_rol, name="usuario-cambiar-rol"),
    path("<int:pk>/desactivar/", v.desactivar, name="usuario-desactivar"),
    path("<int:pk>/reactivar/", v.reactivar, name="usuario-reactivar"),
    path(
        "<int:pk>/restablecer-contrasena/",
        v.restablecer_contrasena,
        name="usuario-restablecer-contrasena",
    ),
]
