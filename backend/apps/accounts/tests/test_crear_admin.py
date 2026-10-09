import logging
from unittest import mock

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.management.commands.crear_admin import VARIABLE_CONTRASENA
from apps.accounts.models import Usuario
from apps.audit.models import Evento

pytestmark = pytest.mark.django_db

CLAVE = "tornillo-azul-77-nube"


def ejecutar(*args, salida=None):
    call_command("crear_admin", *args, stdout=salida)


def test_creates_an_admin_from_the_environment_variable(monkeypatch, capsys):
    monkeypatch.setenv(VARIABLE_CONTRASENA, CLAVE)

    ejecutar("--correo", " Raiz@Empresa.TEST ", "--nombre", "Raíz", "--numero-nomina", "R-1")

    usuario = Usuario.objects.get()
    assert (usuario.correo, usuario.rol, usuario.activo) == ("raiz@empresa.test", "ADMIN", True)
    assert usuario.numero_nomina == "R-1"
    assert usuario.check_password(CLAVE)
    assert CLAVE not in capsys.readouterr().out  # nunca se imprime


def test_it_prompts_for_the_password_when_no_variable_is_set(monkeypatch):
    monkeypatch.delenv(VARIABLE_CONTRASENA, raising=False)

    with mock.patch("getpass.getpass", side_effect=[CLAVE, CLAVE]) as pedir:
        ejecutar("--correo", "raiz@empresa.test", "--nombre", "Raíz")

    assert pedir.call_count == 2
    assert Usuario.objects.get().check_password(CLAVE)


def test_an_empty_variable_counts_as_not_set(monkeypatch):
    monkeypatch.setenv(VARIABLE_CONTRASENA, "")

    with mock.patch("getpass.getpass", side_effect=[CLAVE, CLAVE]):
        ejecutar("--correo", "raiz@empresa.test", "--nombre", "Raíz")

    assert Usuario.objects.get().check_password(CLAVE)


def test_mismatched_passwords_create_nothing(monkeypatch):
    monkeypatch.delenv(VARIABLE_CONTRASENA, raising=False)

    with (
        mock.patch("getpass.getpass", side_effect=[CLAVE, "otra-clave-distinta-1"]),
        pytest.raises(CommandError, match="no coinciden"),
    ):
        ejecutar("--correo", "raiz@empresa.test", "--nombre", "Raíz")

    assert Usuario.objects.count() == 0


def test_a_weak_password_is_rejected_without_echoing_it(monkeypatch):
    monkeypatch.setenv(VARIABLE_CONTRASENA, "qwertyuiop")

    with pytest.raises(CommandError) as error:
        ejecutar("--correo", "raiz@empresa.test", "--nombre", "Raíz")

    assert "qwertyuiop" not in str(error.value)
    assert Usuario.objects.count() == 0


def test_a_duplicate_email_is_a_clean_error(monkeypatch):
    monkeypatch.setenv(VARIABLE_CONTRASENA, CLAVE)
    ejecutar("--correo", "raiz@empresa.test", "--nombre", "Raíz")

    with pytest.raises(CommandError, match="correo"):
        ejecutar("--correo", "RAIZ@empresa.test", "--nombre", "Otra")

    assert Usuario.objects.count() == 1


def test_it_writes_a_system_event_without_the_password(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(VARIABLE_CONTRASENA, CLAVE)

    ejecutar("--correo", "raiz@empresa.test", "--nombre", "Raíz")

    evento = Evento.objects.get()
    assert (evento.tipo, evento.usuario) == ("USUARIO_CREADO", None)  # el Sistema
    assert CLAVE not in f"{evento.descripcion} {evento.antes} {evento.despues} {caplog.text}"


def test_using_the_environment_variable_warns_to_delete_it(monkeypatch, capsys):
    monkeypatch.setenv(VARIABLE_CONTRASENA, CLAVE)

    ejecutar("--correo", "raiz@empresa.test", "--nombre", "Raíz")

    avisos = capsys.readouterr().err
    assert VARIABLE_CONTRASENA in avisos
    assert "Bórrala" in avisos
    assert CLAVE not in avisos


def test_the_interactive_path_prints_no_warning_and_no_password(monkeypatch, capsys):
    monkeypatch.delenv(VARIABLE_CONTRASENA, raising=False)

    with mock.patch("getpass.getpass", side_effect=[CLAVE, CLAVE]):
        ejecutar("--correo", "raiz@empresa.test", "--nombre", "Raíz")

    salida = capsys.readouterr()
    assert salida.err == ""
    assert CLAVE not in salida.out


def test_the_created_admin_logs_in_straight_away_without_a_forced_change(monkeypatch):
    from apps.accounts.tests.conftest import Api

    monkeypatch.setenv(VARIABLE_CONTRASENA, CLAVE)
    ejecutar("--correo", "raiz@empresa.test", "--nombre", "Raíz")

    respuesta = Api().login("raiz@empresa.test", CLAVE)

    assert respuesta.status_code == 200
    assert respuesta.json()["rol"] == "ADMIN"
