import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Usuario

from .factories import DEFAULT_PASSWORD, UsuarioFactory

pytestmark = pytest.mark.django_db


def make(**extra):
    return Usuario.objects.create_user(
        correo=extra.pop("correo", "ana@empresa.test"),
        nombre=extra.pop("nombre", "Ana"),
        rol=extra.pop("rol", Usuario.Rol.TECNICO),
        password=extra.pop("password", DEFAULT_PASSWORD),
        **extra,
    )


def test_auth_user_model_points_to_usuario():
    assert get_user_model() is Usuario


def test_defaults():
    usuario = make()

    assert usuario.activo is True
    assert usuario.is_active is True
    assert usuario.intentos_fallidos == 0
    assert usuario.bloqueado_hasta is None
    assert usuario.debe_cambiar_contrasena is False
    assert usuario.numero_nomina == ""
    assert timezone.is_aware(usuario.creado_en)


def test_has_no_staff_or_superuser_concepts():
    usuario = make()

    assert not hasattr(usuario, "is_staff")
    assert not hasattr(usuario, "is_superuser")
    assert not hasattr(usuario, "has_perm")


def test_email_is_normalized_to_lowercase_on_save():
    usuario = make(correo="  Ana.Perez@Empresa.TEST ")

    assert usuario.correo == "ana.perez@empresa.test"
    usuario.correo = "ANA.PEREZ@EMPRESA.TEST"
    usuario.save()
    usuario.refresh_from_db()
    assert usuario.correo == "ana.perez@empresa.test"


def test_email_unique_ignoring_case_through_manager():
    make(correo="Ana@Empresa.test")

    with pytest.raises(IntegrityError), transaction.atomic():
        make(correo="ANA@empresa.TEST")


def test_email_unique_ignoring_case_enforced_by_database():
    make(correo="ana@empresa.test")

    # bulk_create se salta save(): solo la restricción sobre Lower(correo) lo detiene.
    with pytest.raises(IntegrityError, match="usuario_correo_unico_ci"), transaction.atomic():
        Usuario.objects.bulk_create(
            [Usuario(correo="ANA@empresa.test", nombre="Otra", rol=Usuario.Rol.ADMIN)]
        )


def test_rol_check_constraint_matches_choices():
    with pytest.raises(IntegrityError, match="usuario_rol_valido"), transaction.atomic():
        Usuario.objects.bulk_create([Usuario(correo="x@e.test", nombre="X", rol="SUPERADMIN")])

    constraint = next(c for c in Usuario._meta.constraints if c.name == "usuario_rol_valido")
    assert set(constraint.condition.children[0][1]) == set(Usuario.Rol.values)


def test_failed_attempts_cannot_be_negative():
    with pytest.raises(IntegrityError, match="usuario_intentos_no_negativos"), transaction.atomic():
        Usuario.objects.bulk_create(
            [Usuario(correo="x@e.test", nombre="X", rol="ADMIN", intentos_fallidos=-1)]
        )


@pytest.mark.parametrize("rol", Usuario.Rol.values)
def test_manager_creates_each_role(rol):
    assert make(rol=rol, correo=f"{rol.lower()}@e.test").rol == rol


def test_manager_requires_rol():
    with pytest.raises(TypeError):
        Usuario.objects.create_user(correo="x@e.test", nombre="X")  # type: ignore[call-arg]


@pytest.mark.parametrize("rol", ["", None, "SUPERADMIN", "admin"])
def test_manager_rejects_invalid_rol(rol):
    with pytest.raises(ValueError, match="Rol"):
        make(rol=rol)


@pytest.mark.parametrize("campo", ["correo", "nombre"])
def test_manager_requires_correo_and_nombre(campo):
    with pytest.raises(ValueError, match="obligatorio"):
        make(**{campo: ""})


def test_manager_does_not_create_superusers():
    with pytest.raises(TypeError, match="ADMIN"):
        Usuario.objects.create_superuser("a@e.test", "A", "ADMIN")


def test_user_without_password_has_unusable_password():
    usuario = Usuario.objects.create_user("a@e.test", "A", Usuario.Rol.ADMIN)

    assert not usuario.has_usable_password()


def test_inactive_user_cannot_authenticate():
    usuario = make()
    assert authenticate(correo=usuario.correo, password=DEFAULT_PASSWORD) == usuario

    usuario.activo = False
    usuario.save()

    assert authenticate(correo=usuario.correo, password=DEFAULT_PASSWORD) is None


def test_factory_builds_valid_users():
    usuario = UsuarioFactory(rol=Usuario.Rol.ESPECTADOR)

    assert usuario.check_password(DEFAULT_PASSWORD)
    assert usuario.rol == "ESPECTADOR"


def test_models_have_no_pending_migrations():
    call_command("makemigrations", check=True, dry_run=True, verbosity=0)
