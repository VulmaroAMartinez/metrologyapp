from django.urls import path

from . import views

urlpatterns = [path("eventos/", views.eventos, name="bitacora-eventos")]
