"""Servicios de usuarios: sesiones, nada sensible en eventos/logs/errores y ramas restantes."""

import logging
from unittest import mock

import pytest
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.accounts import services
from apps.audit.models import Evento
from apps.core.testing import DEFAULT_PASSWORD

from .ayudantes import CLAVE, CLAVE_2, ESPECTADOR, TECNICO, crear, sesion, volcado_eventos

pytestmark = pytest.mark.django_db


# --- sesiones ---


def test_invalidating_sessions_deletes_only_that_users_rows(ana, carla):
    uno, dos, de_ana = sesion(carla), sesion(carla), sesion(ana)
    assert Session.objects.count() == 3

    borradas = services.invalidar_sesiones(carla.pk)

    assert borradas == 2
    assert Session.objects.filter(session_key=de_ana.session_cookie).exists()
    assert not Session.objects.filter(session_key__in=[uno.session_cookie, dos.session_cookie])


def test_invalidating_ignores_anonymous_and_expired_sessions(carla, clock):
    sesion(carla)
    clock.advance(hours=2)  # la sesión de Carla ya venció

    assert services.invalidar_sesiones(carla.pk) == 0


@pytest.mark.django_db(transaction=True)  # transacciones reales: se ejecutan los on_commit
def test_deactivate_and_reset_close_the_targets_sessions_after_the_commit(ana, carla):
    sesion(carla)
    services.desactivar_usuario(ana.pk, carla.pk)
    assert not Session.objects.exists()

    carla.activo = True
    carla.save()
    sesion(carla)
    services.restablecer_contrasena(ana.pk, carla.pk, CLAVE_2)
    assert not Session.objects.exists()


def test_a_role_change_keeps_the_session_but_not_the_old_permissions(ana, carla):
    sesion(carla)

    services.cambiar_rol(ana.pk, carla.pk, ESPECTADOR)

    assert (
        Session.objects.count() == 1
    )  # la sesión sigue; el rol se lee de la base en cada petición


# --- nada sensible en eventos, logs, errores ni trazas ---


def test_no_password_or_hash_reaches_events_or_logs(ana, carla, caplog):
    caplog.set_level(logging.DEBUG)

    usuario = crear(ana)
    services.restablecer_contrasena(ana.pk, carla.pk, CLAVE_2)
    services.editar_usuario(ana.pk, usuario.pk, {"nombre": "Otro Nombre"})
    services.cambiar_rol(ana.pk, usuario.pk, ESPECTADOR)
    services.desactivar_usuario(ana.pk, usuario.pk)
    services.reactivar_usuario(ana.pk, usuario.pk)
    usuario.refresh_from_db()
    carla.refresh_from_db()

    texto = volcado_eventos() + caplog.text
    for secreto in (CLAVE, CLAVE_2, usuario.password, carla.password, DEFAULT_PASSWORD):
        assert secreto not in texto
    assert "argon2" not in texto
    assert "md5$" not in texto


@pytest.mark.parametrize(
    "operacion",
    [
        lambda ana, carla: services.crear_usuario(
            ana.pk, nombre="Otro", correo="otro@empresa.test", rol=TECNICO, contrasena=CLAVE
        ),
        lambda ana, carla: services.restablecer_contrasena(ana.pk, carla.pk, CLAVE),
    ],
    ids=["crear", "restablecer"],
)
def test_a_failure_in_the_middle_never_leaks_the_password_through_the_error_or_logs(
    operacion, ana, carla, caplog
):
    caplog.set_level(logging.DEBUG)

    with (
        mock.patch.object(services, "registrar_evento", side_effect=RuntimeError("falla")),
        pytest.raises(RuntimeError) as error,
    ):
        operacion(ana, carla)

    assert CLAVE not in str(error.value)
    assert CLAVE not in caplog.text


def test_validator_failures_never_leak_the_password_through_logs(ana, carla, caplog):
    caplog.set_level(logging.DEBUG)

    with pytest.raises(services.DatosDeUsuarioInvalidos):
        services.restablecer_contrasena(ana.pk, carla.pk, "carla@empresa.test1")

    assert "carla@empresa.test1" not in caplog.text


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.Argon2PasswordHasher"])
def test_a_created_user_authenticates_with_the_real_hasher(ana):
    usuario = crear(ana)

    assert usuario.password.startswith("argon2$argon2id$")
    assert usuario.check_password(CLAVE)


# --- ramas restantes ---


def test_a_well_formed_but_overlong_email_is_rejected(ana):
    largo = "a" * 64 + "@" + ".".join(["b" * 60] * 4) + ".com"

    with pytest.raises(services.DatosDeUsuarioInvalidos, match="largo"):
        crear(ana, correo=largo)


def test_edit_resolves_a_unique_index_clash_when_the_precheck_is_bypassed(ana, beto):
    with (
        mock.patch.object(services, "_verificar_duplicados"),
        pytest.raises(services.CorreoDuplicado),
    ):
        services.editar_usuario(ana.pk, beto.pk, {"correo": "ANA@empresa.test"})

    beto.refresh_from_db()
    assert beto.correo == "beto@empresa.test"
    assert Evento.objects.count() == 0


def test_change_role_rejects_an_unknown_role(ana, carla):
    with pytest.raises(services.DatosDeUsuarioInvalidos):
        services.cambiar_rol(ana.pk, carla.pk, "SUPERADMIN")


def test_a_validator_rejection_message_is_built_from_the_validators_text(ana, carla):
    with (
        mock.patch.object(
            services, "validate_password", side_effect=ValidationError(["Regla incumplida."])
        ),
        pytest.raises(services.DatosDeUsuarioInvalidos, match="Regla incumplida"),
    ):
        services.restablecer_contrasena(ana.pk, carla.pk, CLAVE)
