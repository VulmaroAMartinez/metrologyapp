import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("metodo", "ruta", "estado"),
    [
        ("get", "/api/v1/health/", 200),
        ("get", "/api/v1/auth/csrf/", 200),
        ("get", "/api/v1/auth/me/", 401),  # error de autenticación
        ("get", "/api/v1/usuarios/", 401),
        ("get", "/api/v1/bitacora/eventos/", 401),
        ("post", "/api/v1/auth/login/", 403),  # rechazo de CSRF generado por DRF
        ("get", "/api/v1/auth/login/", 405),
        ("get", "/api/v1/no-existe/", 404),  # 404 de Django (no pasa por DRF)
    ],
)
def test_every_api_response_is_marked_no_store(metodo, ruta, estado):
    respuesta = getattr(Client(enforce_csrf_checks=True), metodo)(ruta)

    assert respuesta.status_code == estado
    assert respuesta["Cache-Control"] == "no-store"


def test_responses_outside_the_api_are_left_alone():
    respuesta = Client().get("/fuera-de-la-api/")

    assert respuesta.status_code == 404 and "Cache-Control" not in respuesta
