"""API de usuarios, escritura: crear y editar (la contraseña la escribe el administrador)."""

import pytest

from apps.accounts.models import Usuario
from apps.audit.models import Evento
from apps.core.testing import Api, UsuarioFactory

from .ayudantes import BASE, CAMPOS, CLAVE, nuevo, ruta

pytestmark = pytest.mark.django_db


# --- crear ---


def test_create_stores_the_password_the_admin_wrote_and_never_returns_it(api_admin):
    respuesta = api_admin.post(BASE, nuevo(numero_nomina=" N-7 "))

    cuerpo = respuesta.json()
    assert respuesta.status_code == 201
    assert respuesta["Cache-Control"] == "no-store"
    assert set(cuerpo) == CAMPOS  # ningún campo de contraseña, ni temporal ni de otro tipo
    assert cuerpo["activo"] is True
    assert cuerpo["numero_nomina"] == "N-7"
    assert cuerpo["bloqueado_hasta"] is None
    assert CLAVE not in respuesta.content.decode()
    for texto in (api_admin.get(ruta(cuerpo["id"])).content, api_admin.get(BASE).content):
        assert CLAVE.encode() not in texto


def test_the_created_user_logs_in_straight_away_with_the_password_the_admin_wrote(api_admin):
    api_admin.post(BASE, nuevo())

    respuesta = Api().login("luis@empresa.test", CLAVE)

    assert respuesta.status_code == 200
    assert set(respuesta.json()) == {"id", "nombre", "correo", "rol", "numero_nomina"}


@pytest.mark.parametrize(
    "campo", ["password", "debe_cambiar_contrasena", "contrasena_temporal", "activo", "id"]
)
def test_create_rejects_fields_that_are_not_part_of_the_form(api_admin, campo):
    respuesta = api_admin.post(BASE, nuevo(**{campo: "x"}))

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert Usuario.objects.count() == 1


def test_create_requires_the_password(api_admin):
    cuerpo = nuevo()
    del cuerpo["contrasena"]

    respuesta = api_admin.post(BASE, cuerpo)

    assert respuesta.status_code == 400
    assert "contrasena" in respuesta.json()["campos"]


@pytest.mark.parametrize(
    ("contrasena", "fragmento"),
    [
        ("Ab1-cdef9", "10 caracteres"),
        ("qwertyuiop", "muy común"),
        ("48291746503", "numérica"),
        ("luis@empresa.test1", "muy similar"),
    ],
)
def test_create_runs_the_password_through_the_validators(api_admin, contrasena, fragmento):
    respuesta = api_admin.post(BASE, nuevo(contrasena=contrasena))

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert any(fragmento in m for m in respuesta.json()["campos"]["contrasena"])
    assert contrasena not in respuesta.content.decode()  # el mensaje no repite la contraseña
    assert Usuario.objects.count() == 1
    assert not Evento.objects.filter(tipo="USUARIO_CREADO").exists()


def test_an_overlong_password_is_rejected_before_reaching_the_hasher(api_admin):
    respuesta = api_admin.post(BASE, nuevo(contrasena="a1" * 200))

    assert respuesta.status_code == 400


def test_blank_nomina_is_stored_as_null_and_the_name_is_trimmed(api_admin):
    cuerpo = api_admin.post(BASE, nuevo(nombre="  Luis  ", numero_nomina="   ")).json()

    assert cuerpo["numero_nomina"] is None
    assert cuerpo["nombre"] == "Luis"


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"nombre": "", "correo": "a@e.test", "rol": "TECNICO", "contrasena": CLAVE},
        {"nombre": "   ", "correo": "a@e.test", "rol": "TECNICO", "contrasena": CLAVE},
        {"nombre": "x" * 151, "correo": "a@e.test", "rol": "TECNICO", "contrasena": CLAVE},
        {"nombre": "A", "correo": "no-es-correo", "rol": "TECNICO", "contrasena": CLAVE},
        {"nombre": "A", "correo": "a@e.test", "rol": "SUPERADMIN", "contrasena": CLAVE},
        {"nombre": "A", "correo": "a@e.test", "contrasena": CLAVE},
        {
            "nombre": "A",
            "correo": "a@e.test",
            "rol": "TECNICO",
            "contrasena": CLAVE,
            "numero_nomina": "9" * 21,
        },
    ],
)
def test_invalid_create_bodies_are_400(api_admin, cuerpo):
    respuesta = api_admin.post(BASE, cuerpo)

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert Evento.objects.filter(tipo="USUARIO_CREADO").count() == 0


@pytest.mark.parametrize(
    "correo", ["luis@empresa.test", "LUIS@Empresa.TEST", " luis@empresa.test "]
)
def test_duplicate_email_is_a_clear_400_never_a_500(api_admin, correo):
    api_admin.post(BASE, nuevo())

    respuesta = api_admin.post(BASE, nuevo(correo=correo, nombre="Otro"))

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "correo_duplicado"
    assert Usuario.objects.filter(correo="luis@empresa.test").count() == 1


@pytest.mark.parametrize("nomina", ["N-7", "n-7", "  N-7  "])
def test_duplicate_nomina_is_a_clear_400(api_admin, nomina):
    api_admin.post(BASE, nuevo(numero_nomina="N-7"))

    respuesta = api_admin.post(BASE, nuevo(correo="otro@empresa.test", numero_nomina=nomina))

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "numero_nomina_duplicado"


# --- editar ---


def test_edit_updates_and_normalizes(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test", nombre="Beto")

    respuesta = api_admin.call(
        "PATCH",
        ruta(objetivo.pk),
        {"nombre": " Beto Ruiz ", "correo": " BETO.R@Empresa.test ", "numero_nomina": " 55 "},
    )

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200
    assert (cuerpo["nombre"], cuerpo["correo"], cuerpo["numero_nomina"]) == (
        "Beto Ruiz",
        "beto.r@empresa.test",
        "55",
    )
    assert Evento.objects.get(tipo="USUARIO_EDITADO").usuario.correo == "admin@empresa.test"


def test_edit_can_clear_the_nomina(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test", numero_nomina="55")

    cuerpo = api_admin.call("PATCH", ruta(objetivo.pk), {"numero_nomina": ""}).json()

    assert cuerpo["numero_nomina"] is None


@pytest.mark.parametrize(
    "campo",
    [
        "rol",
        "activo",
        "password",
        "contrasena",
        "bloqueado_hasta",
        "intentos_fallidos",
    ],
)
def test_edit_rejects_fields_that_have_their_own_endpoint_or_are_read_only(api_admin, campo):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    respuesta = api_admin.call("PATCH", ruta(objetivo.pk), {campo: "x"})

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"


def test_edit_duplicates_and_unknown_user(api_admin):
    uno = UsuarioFactory(correo="uno@empresa.test", numero_nomina="U-1")
    dos = UsuarioFactory(correo="dos@empresa.test")

    por_correo = api_admin.call("PATCH", ruta(dos.pk), {"correo": "UNO@empresa.test"})
    por_nomina = api_admin.call("PATCH", ruta(dos.pk), {"numero_nomina": " u-1 "})
    inexistente = api_admin.call("PATCH", ruta(987654), {"nombre": "X"})

    assert (por_correo.status_code, por_correo.json()["codigo"]) == (400, "correo_duplicado")
    assert (por_nomina.status_code, por_nomina.json()["codigo"]) == (400, "numero_nomina_duplicado")
    assert inexistente.status_code == 404
    assert uno.pk != dos.pk


def test_an_empty_edit_changes_nothing_and_writes_no_event(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    assert api_admin.call("PATCH", ruta(objetivo.pk), {}).status_code == 200
    assert Evento.objects.filter(tipo="USUARIO_EDITADO").count() == 0
