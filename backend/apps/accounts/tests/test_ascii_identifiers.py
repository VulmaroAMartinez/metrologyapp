"""Decisión de Vul: correo y numero_nomina solo admiten caracteres ASCII.

El nombre sí puede llevar acentos y eñes. La regla vive en los servicios (como el resto de las
reglas de negocio); `.lower()` de Python y `LOWER()` de PostgreSQL no coinciden con caracteres
no ASCII, así que dejarlos pasar rompería la unicidad sin distinguir mayúsculas.
"""

import pytest

from apps.accounts import services
from apps.accounts.models import Usuario
from apps.core.testing import Api

pytestmark = pytest.mark.django_db

CLAVE = "tornillo-azul-77-nube"
BASE = "/api/v1/usuarios/"
CORREOS_NO_ASCII = [
    "josé@empresa.test",
    "ana@empresa.tést",
    "\uff41\uff4e\uff41@empresa.test",  # anchura completa: parece ASCII pero no lo es
    "ana@empresa.test\u200b",  # espacio de ancho cero
    "İlker@empresa.test",  # la I turca con punto: .lower() y LOWER() difieren
]
NOMINAS_NO_ASCII = ["Ñ-001", "N-\uff10\uff10\uff11", "N-001é", "N\u200b001"]


def crear(actor, **extra):
    datos = {
        "nombre": "Nuevo",
        "correo": "nuevo@empresa.test",
        "rol": Usuario.Rol.TECNICO,
        "contrasena": CLAVE,
        **extra,
    }
    return services.crear_usuario(actor.pk, **datos)


@pytest.mark.parametrize("correo", CORREOS_NO_ASCII)
def test_create_rejects_a_non_ascii_email(ana, correo):
    with pytest.raises(services.DatosDeUsuarioInvalidos, match="ASCII"):
        crear(ana, correo=correo)

    assert Usuario.objects.count() == 1


@pytest.mark.parametrize("nomina", NOMINAS_NO_ASCII)
def test_create_rejects_a_non_ascii_payroll_number(ana, nomina):
    with pytest.raises(services.DatosDeUsuarioInvalidos, match="ASCII"):
        crear(ana, numero_nomina=nomina)

    assert Usuario.objects.count() == 1


@pytest.mark.parametrize("correo", CORREOS_NO_ASCII)
def test_edit_rejects_a_non_ascii_email(ana, carla, correo):
    with pytest.raises(services.DatosDeUsuarioInvalidos, match="ASCII"):
        services.editar_usuario(ana.pk, carla.pk, {"correo": correo})

    carla.refresh_from_db()
    assert carla.correo == "carla@empresa.test"


@pytest.mark.parametrize("nomina", NOMINAS_NO_ASCII)
def test_edit_rejects_a_non_ascii_payroll_number(ana, carla, nomina):
    with pytest.raises(services.DatosDeUsuarioInvalidos, match="ASCII"):
        services.editar_usuario(ana.pk, carla.pk, {"numero_nomina": nomina})

    carla.refresh_from_db()
    assert carla.numero_nomina is None


def test_the_name_may_keep_accents(ana):
    usuario = crear(ana, nombre="José Ñandú Pérez")

    assert usuario.nombre == "José Ñandú Pérez"


def test_ascii_values_still_work_in_any_case(ana):
    usuario = crear(ana, correo="Luis.Perez@Empresa.TEST", numero_nomina="n-001")

    assert (usuario.correo, usuario.numero_nomina) == ("luis.perez@empresa.test", "n-001")


def test_the_api_answers_400_for_a_non_ascii_payroll_number(ana, settings):
    settings.LOGIN_RATE_PER_IP_EMAIL = settings.LOGIN_RATE_PER_IP = 1000
    api = Api()
    api.login(ana.correo)

    respuesta = api.post(
        BASE,
        {
            "nombre": "Luis",
            "correo": "luis@empresa.test",
            "rol": "TECNICO",
            "contrasena": CLAVE,
            "numero_nomina": "Ñ-001",
        },
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"
    assert "ASCII" in str(respuesta.json()["detalle"])


@pytest.mark.parametrize(
    ("correo", "estado", "codigo"),
    [
        ("ana@empresa.tést", 401, "credenciales_invalidas"),  # formato válido (IDN): no existe
        ("josé@empresa.test", 400, "datos_invalidos"),  # el formato ya lo rechaza el login
    ],
)
def test_logging_in_with_a_non_ascii_email_never_succeeds(ana, settings, correo, estado, codigo):
    settings.LOGIN_RATE_PER_IP_EMAIL = settings.LOGIN_RATE_PER_IP = 1000
    api = Api()

    respuesta = api.login(correo, CLAVE)

    assert respuesta.status_code == estado
    assert respuesta.json()["codigo"] == codigo
