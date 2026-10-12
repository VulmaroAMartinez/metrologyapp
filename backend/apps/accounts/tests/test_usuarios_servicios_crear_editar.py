"""Servicios de usuarios: crear, editar, normalización, contraseñas y duplicados."""

import pytest
from django.db import IntegrityError

from apps.accounts import services
from apps.accounts.models import Usuario
from apps.accounts.services import usuarios
from apps.audit.models import Evento

from .ayudantes import ADMIN, CLAVE, crear, instantanea, lanza, sin_comprobar_duplicados

pytestmark = pytest.mark.django_db


# --- no quedan restos del modelo anterior ---


def test_there_is_no_forced_change_and_no_temporary_password_left():
    campos = {campo.name for campo in Usuario._meta.get_fields()}

    assert "debe_cambiar_contrasena" not in campos
    assert not hasattr(services, "generar_contrasena_temporal")
    assert not hasattr(services, "PasswordChangeNotRequired")


# --- crear: normalización y validación ---


def test_create_normalizes_fields_and_stores_the_password_the_admin_wrote(ana):
    usuario = crear(
        ana, nombre="  Luis  Pérez ", correo="  Luis.Perez@Empresa.TEST ", numero_nomina=" A-77 "
    )

    usuario.refresh_from_db()
    assert (usuario.nombre, usuario.correo, usuario.numero_nomina) == (
        "Luis  Pérez",
        "luis.perez@empresa.test",
        "A-77",
    )
    assert usuario.activo is True
    assert usuario.check_password(CLAVE)
    assert (usuario.intentos_fallidos, usuario.bloqueado_hasta) == (0, None)


@pytest.mark.parametrize("vacio", ["", "   ", None])
def test_blank_nomina_becomes_null(ana, vacio):
    usuario = crear(ana, numero_nomina=vacio)

    usuario.refresh_from_db()
    assert usuario.numero_nomina is None


@pytest.mark.parametrize(
    ("campos", "fragmento"),
    [
        ({"nombre": ""}, "nombre"),
        ({"nombre": "   "}, "nombre"),
        ({"nombre": "x" * 151}, "150"),
        ({"correo": "no-es-correo"}, "correo"),
        ({"correo": ""}, "correo"),
        ({"correo": "a@" + "b" * 250 + ".com"}, "correo"),
        ({"rol": "SUPERADMIN"}, "rol"),
        ({"numero_nomina": "9" * 21}, "20"),
    ],
)
def test_create_rejects_invalid_data(ana, campos, fragmento):
    with pytest.raises(services.DatosDeUsuarioInvalidos) as error:
        crear(ana, **campos)

    assert fragmento in error.value.detalle.lower()
    assert Usuario.objects.count() == 1
    assert Evento.objects.count() == 0


def test_name_at_the_maximum_length_is_accepted(ana):
    assert len(crear(ana, nombre="x" * 150).nombre) == 150


# --- la contraseña la escribe el administrador y pasa por los validadores ---


@pytest.mark.parametrize(
    ("contrasena", "fragmento"),
    [
        ("Ab1-cdef9", "10 caracteres"),
        ("qwertyuiop", "muy común"),
        ("48291746503", "numérica"),
        ("nuevo@empresa.test1", "muy similar"),
    ],
)
def test_create_runs_the_password_validators_without_echoing_it(ana, contrasena, fragmento):
    with pytest.raises(services.DatosDeUsuarioInvalidos) as error:
        crear(ana, contrasena=contrasena)

    assert fragmento in error.value.detalle
    assert contrasena not in error.value.detalle
    assert contrasena not in str(error.value)
    assert Usuario.objects.count() == 1
    assert Evento.objects.count() == 0


@pytest.mark.parametrize(
    ("contrasena", "fragmento"),
    [("Ab1-cdef9", "10 caracteres"), ("qwertyuiop", "muy común"), ("48291746503", "numérica")],
)
def test_reset_runs_the_password_validators_without_echoing_it(ana, carla, contrasena, fragmento):
    antes = instantanea()

    with pytest.raises(services.DatosDeUsuarioInvalidos) as error:
        services.restablecer_contrasena(ana.pk, carla.pk, contrasena)

    assert fragmento in error.value.detalle
    assert contrasena not in error.value.detalle
    assert instantanea() == antes


def test_reset_rejects_a_password_similar_to_the_targets_own_data(ana, carla):
    with pytest.raises(services.DatosDeUsuarioInvalidos, match="muy similar"):
        services.restablecer_contrasena(ana.pk, carla.pk, "carla@empresa.test1")


def test_system_created_user_validates_the_given_password_without_echoing_it():
    with pytest.raises(services.DatosDeUsuarioInvalidos) as error:
        services.crear_usuario(
            None, nombre="Raíz", correo="raiz@empresa.test", rol=ADMIN, contrasena="qwertyuiop"
        )

    assert "qwertyuiop" not in error.value.detalle


def test_system_created_admin_has_no_actor():
    usuario = services.crear_usuario(
        None, nombre="Raíz", correo="raiz@empresa.test", rol=ADMIN, contrasena=CLAVE
    )

    assert usuario.check_password(CLAVE)
    evento = Evento.objects.get()
    assert (evento.tipo, evento.usuario) == ("USUARIO_CREADO", None)


# --- duplicados ---


@pytest.mark.parametrize(
    "variante", ["ana@empresa.test", "ANA@Empresa.TEST", "  ana@empresa.test  "]
)
def test_duplicate_email_ignores_case_and_spaces(ana, variante):
    with pytest.raises(services.CorreoDuplicado) as error:
        crear(ana, correo=variante)

    assert error.value.codigo == "correo_duplicado"
    assert error.value.estado == 400


@pytest.mark.parametrize("variante", ["ab-12", "AB-12", "  ab-12 ", "Ab-12"])
def test_duplicate_nomina_ignores_case_and_spaces(ana, variante):
    crear(ana, correo="uno@empresa.test", numero_nomina="AB-12")

    with pytest.raises(services.NominaDuplicada) as error:
        crear(ana, correo="dos@empresa.test", numero_nomina=variante)

    assert error.value.codigo == "numero_nomina_duplicado"
    assert error.value.estado == 400


def test_the_unique_index_is_the_last_line_of_defense_for_simultaneous_submissions(ana):
    # Sin la comprobación previa (lo que pasaría con dos envíos simultáneos), el índice único
    # lanza IntegrityError y se traduce al mismo error limpio: nunca un 500.
    crear(ana, correo="uno@empresa.test", numero_nomina="N-1")

    with sin_comprobar_duplicados():
        with pytest.raises(services.CorreoDuplicado):
            crear(ana, correo="UNO@empresa.test")
        with pytest.raises(services.NominaDuplicada):
            crear(ana, correo="otro@empresa.test", numero_nomina="n-1")


def test_unrecognized_integrity_errors_are_not_disguised(ana):
    with (
        usuarios.sustituir(crear_usuario_en_bd=lanza(IntegrityError("otra"))),
        pytest.raises(IntegrityError, match="otra"),
    ):
        crear(ana)


def test_edit_cannot_take_another_users_email_or_nomina(ana, beto):
    beto.numero_nomina = "B-1"
    beto.save()

    with pytest.raises(services.CorreoDuplicado):
        services.editar_usuario(ana.pk, ana.pk, {"correo": " BETO@empresa.test"})
    with pytest.raises(services.NominaDuplicada):
        services.editar_usuario(ana.pk, ana.pk, {"numero_nomina": "b-1"})


def test_edit_can_keep_own_email_in_another_case(ana):
    resultado = services.editar_usuario(ana.pk, ana.pk, {"correo": "ANA@empresa.test"})

    assert resultado.correo == "ana@empresa.test"
    assert Evento.objects.count() == 0  # no hubo cambio real
