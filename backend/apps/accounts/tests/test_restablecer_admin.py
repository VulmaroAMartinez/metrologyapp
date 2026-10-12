"""manage.py restablecer_admin <correo>: recuperación desde el servidor, solo para ADMIN."""

import logging
from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.models import Usuario
from apps.audit.models import Evento
from apps.core.testing import DEFAULT_PASSWORD, ME, Api, UsuarioFactory

from .ayudantes import evento_que_falla

pytestmark = pytest.mark.django_db

NUEVA = "tornillo-azul-77-nube"


def ejecutar(correo, claves=(NUEVA, NUEVA)):
    with mock.patch("getpass.getpass", side_effect=list(claves)) as pedir:
        call_command("restablecer_admin", correo)
    return pedir


def test_it_asks_twice_without_echo_and_sets_the_new_password(admin, capsys):
    pedir = ejecutar(" ADMIN@Empresa.TEST ")

    admin.refresh_from_db()
    assert pedir.call_count == 2  # getpass: sin eco
    assert admin.check_password(NUEVA)
    assert not admin.check_password(DEFAULT_PASSWORD)
    salida = capsys.readouterr().out
    assert "admin@empresa.test" in salida
    assert NUEVA not in salida
    assert Api().login(admin.correo, NUEVA).status_code == 200  # entra directo, sin cambio forzado


def test_it_only_works_for_administrators(db):
    tecnico = UsuarioFactory(correo="tecnico@empresa.test", rol=Usuario.Rol.TECNICO)

    with pytest.raises(CommandError, match="No existe un administrador"):
        ejecutar(tecnico.correo)

    tecnico.refresh_from_db()
    assert tecnico.check_password(DEFAULT_PASSWORD)
    assert Evento.objects.count() == 0


def test_an_unknown_email_gives_the_same_error(db):
    with pytest.raises(CommandError, match="No existe un administrador"):
        ejecutar("nadie@empresa.test")


def test_mismatched_passwords_change_nothing(admin):
    with pytest.raises(CommandError, match="no coinciden"):
        ejecutar(admin.correo, claves=(NUEVA, "otra-clave-distinta-1"))

    admin.refresh_from_db()
    assert admin.check_password(DEFAULT_PASSWORD)
    assert Evento.objects.count() == 0


def test_the_new_password_goes_through_the_validators_without_being_echoed(admin, capsys):
    with pytest.raises(CommandError) as error:
        ejecutar(admin.correo, claves=("qwertyuiop", "qwertyuiop"))

    assert "muy común" in str(error.value)
    assert "qwertyuiop" not in str(error.value)
    assert "qwertyuiop" not in capsys.readouterr().out
    admin.refresh_from_db()
    assert admin.check_password(DEFAULT_PASSWORD)


def test_it_does_not_touch_the_lock_counters_or_the_lock(admin):
    bloqueo = timezone.now() + timedelta(minutes=10)
    Usuario.objects.filter(pk=admin.pk).update(intentos_fallidos=5, bloqueado_hasta=bloqueo)

    ejecutar(admin.correo)

    admin.refresh_from_db()
    assert (admin.intentos_fallidos, admin.bloqueado_hasta) == (5, bloqueo)
    assert Api().login(admin.correo, NUEVA).status_code == 401  # sigue bloqueada: no es desbloqueo


def test_it_closes_the_admins_open_sessions(admin, relaxed_rate):
    abierta = Api()
    assert abierta.login(admin.correo).status_code == 200
    assert abierta.get(ME).status_code == 200

    ejecutar(admin.correo)

    assert abierta.get(ME).status_code == 401


def test_it_writes_a_system_event_saying_it_was_a_server_command(admin):
    ejecutar(admin.correo)

    evento = Evento.objects.get()
    assert evento.tipo == "USUARIO_CONTRASENA_RESTABLECIDA"
    assert evento.usuario is None  # el Sistema
    assert "por comando de servidor" in evento.descripcion
    assert "admin@empresa.test" in evento.descripcion
    assert (evento.antes, evento.despues) == (None, None)


def test_the_password_never_reaches_events_or_logs(admin, caplog):
    caplog.set_level(logging.DEBUG)

    ejecutar(admin.correo)

    admin.refresh_from_db()
    texto = str(list(Evento.objects.values())) + caplog.text
    assert NUEVA not in texto
    assert admin.password not in texto


def test_if_the_event_fails_the_password_is_not_changed(admin):
    with (
        evento_que_falla(),
        pytest.raises(RuntimeError),
    ):
        ejecutar(admin.correo)

    admin.refresh_from_db()
    assert admin.check_password(DEFAULT_PASSWORD)


def test_it_works_for_the_only_admin_in_the_system(admin):
    assert Usuario.objects.filter(rol="ADMIN").count() == 1

    ejecutar(admin.correo)

    admin.refresh_from_db()
    assert admin.check_password(NUEVA)
    assert admin.activo is True
