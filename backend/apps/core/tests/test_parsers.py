"""La API solo acepta cuerpos JSON: sin formularios ni multipart (superficie innecesaria)."""

import pytest

from apps.core.testing import LOGIN, Api

pytestmark = pytest.mark.django_db

BORDE = "BORDE"
MULTIPART = (
    f'--{BORDE}\r\nContent-Disposition: form-data; name="correo"\r\n\r\na@e.test\r\n'
    f'--{BORDE}\r\nContent-Disposition: form-data; name="contrasena"\r\n\r\nx\r\n--{BORDE}--\r\n'
)


def test_the_only_default_parser_is_json(settings):
    assert settings.REST_FRAMEWORK["DEFAULT_PARSER_CLASSES"] == [
        "rest_framework.parsers.JSONParser"
    ]


@pytest.mark.parametrize(
    ("tipo", "cuerpo"),
    [
        ("application/x-www-form-urlencoded", "correo=a%40e.test&contrasena=x"),
        (f"multipart/form-data; boundary={BORDE}", MULTIPART),
        ("text/plain", "correo=a@e.test"),
    ],
)
def test_non_json_bodies_get_415_even_with_a_valid_csrf_token(tipo, cuerpo):
    api = Api()
    api.csrf()

    respuesta = api.client.post(LOGIN, cuerpo, content_type=tipo, HTTP_X_CSRFTOKEN=api.token)

    assert respuesta.status_code == 415
    assert respuesta.json()["codigo"] == "tipo_no_soportado"


def test_json_bodies_keep_working():
    api = Api()

    respuesta = api.post(LOGIN, {"correo": "nadie@e.test", "contrasena": "x"})

    assert respuesta.status_code == 401  # llegó al login: solo credenciales inválidas
