from django.urls import path

from . import views

urlpatterns = [
    path("csrf/", views.csrf, name="auth-csrf"),
    path("login/", views.login, name="auth-login"),
    path("logout/", views.logout, name="auth-logout"),
    path("me/", views.me, name="auth-me"),
    path("cambiar-contrasena/", views.cambiar_contrasena, name="auth-cambiar-contrasena"),
]
