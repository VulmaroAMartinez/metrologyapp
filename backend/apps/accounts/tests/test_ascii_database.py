"""Correo y numero_nomina solo ASCII en la BASE DE DATOS (CHECK), y su normalización coherente
con los índices únicos sin distinguir mayúsculas (`Lower(...)`).

La regla de negocio vive en los servicios; el CHECK es la red de seguridad si algo escribe sin
pasar por ellos (shell, importaciones, un servicio futuro).
"""

import pytest
from django.db import IntegrityError, connection, transaction

from apps.accounts import services
from apps.accounts.models import Usuario

from .factories import UsuarioFactory

pytestmark = pytest.mark.django_db

CLAVE = "tornillo-azul-77-nube"


def sin_normalizar(**campos):
    """Un usuario escrito directo con bulk_create: sin `save()` y sin servicios."""
    datos = {"nombre": "X", "rol": "TECNICO", "password": "!"}
    return Usuario(**{**datos, **campos})


# --- el CHECK de la base de datos --------------------------------------------------------------


@pytest.mark.parametrize(
    ("campos", "restriccion"),
    [
        ({"correo": "josé@empresa.test"}, "usuario_correo_ascii"),
        ({"correo": "ana@empresa.tést"}, "usuario_correo_ascii"),
        ({"correo": "İlker@empresa.test"}, "usuario_correo_ascii"),
        ({"correo": "ana@empresa.test​"}, "usuario_correo_ascii"),
        ({"correo": "a@e.test", "numero_nomina": "Ñ-1"}, "usuario_nomina_ascii"),
        ({"correo": "b@e.test", "numero_nomina": "N-\uff11"}, "usuario_nomina_ascii"),
    ],
)
def test_the_database_rejects_non_ascii_even_without_the_services(campos, restriccion):
    with pytest.raises(IntegrityError, match=restriccion), transaction.atomic():
        Usuario.objects.bulk_create([sin_normalizar(**campos)])

    assert Usuario.objects.count() == 0


def test_the_database_rejects_a_non_ascii_update_too():
    usuario = UsuarioFactory(correo="ana@empresa.test")

    with pytest.raises(IntegrityError, match="usuario_correo_ascii"), transaction.atomic():
        Usuario.objects.filter(pk=usuario.pk).update(correo="ana@empresa.tést")
    with pytest.raises(IntegrityError, match="usuario_nomina_ascii"), transaction.atomic():
        Usuario.objects.filter(pk=usuario.pk).update(numero_nomina="Ñ-2")

    usuario.refresh_from_db()
    assert usuario.correo == "ana@empresa.test"
    assert usuario.numero_nomina is None


def test_ascii_values_and_null_payroll_numbers_are_accepted():
    Usuario.objects.bulk_create(
        [
            sin_normalizar(correo="a@e.test", numero_nomina=None),
            sin_normalizar(correo="b@e.test", numero_nomina="N-001/ab_c"),
        ]
    )

    assert Usuario.objects.count() == 2


# --- la normalización de Python coincide con LOWER() de PostgreSQL ------------------------------


def lower_de_postgres(texto: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT lower(%s)", [texto])
        return cursor.fetchone()[0]


def test_python_and_postgres_lowercase_every_ascii_character_the_same():
    diferencias = [
        codigo for codigo in range(1, 128) if chr(codigo).lower() != lower_de_postgres(chr(codigo))
    ]

    assert diferencias == []


@pytest.mark.parametrize("campo", ["correo", "numero_nomina"])
def test_unique_indexes_reject_a_case_variant_even_if_the_service_is_bypassed(campo):
    base = {"correo": "Ana@Empresa.Test", "numero_nomina": "N-Aa1"}
    Usuario.objects.bulk_create([sin_normalizar(**base)])
    variante = {"correo": "other@e.test", "numero_nomina": "other", campo: base[campo].swapcase()}

    with pytest.raises(IntegrityError, match=r"unico|unica"), transaction.atomic():
        Usuario.objects.bulk_create([sin_normalizar(**variante)])


def test_the_service_normalizes_to_what_the_unique_index_compares():
    admin = UsuarioFactory(correo="admin@empresa.test", rol="ADMIN")

    creado = services.crear_usuario(
        admin.pk,
        nombre="Luis",
        correo="  Luis.Perez@EMPRESA.Test ",
        rol="TECNICO",
        contrasena=CLAVE,
        numero_nomina=" n-Ab1 ",
    )

    assert creado.correo == lower_de_postgres(creado.correo) == "luis.perez@empresa.test"
    with pytest.raises(services.CorreoDuplicado):
        services.crear_usuario(
            admin.pk, nombre="L2", correo="LUIS.PEREZ@empresa.test", rol="TECNICO", contrasena=CLAVE
        )
    with pytest.raises(services.NominaDuplicada):
        services.crear_usuario(
            admin.pk,
            nombre="L3",
            correo="otro@empresa.test",
            rol="TECNICO",
            contrasena=CLAVE,
            numero_nomina="N-AB1",
        )
