"""Servicios de usuarios: eventos de cada servicio y atomicidad cambio + evento."""

import pytest
from django.contrib.sessions.models import Session

from apps.accounts import services
from apps.accounts.services import usuarios
from apps.audit.models import Evento

from .ayudantes import (
    CLAVE,
    CLAVE_2,
    ESPECTADOR,
    OPERACIONES,
    TECNICO,
    evento_que_falla,
    instantanea,
    sesion,
    volcado_eventos,
)

pytestmark = pytest.mark.django_db


# --- eventos y restricciones por servicio ---


def test_every_service_writes_its_event_with_actor_and_ip(ana, carla):
    usuario = services.crear_usuario(
        ana.pk,
        nombre="Ex",
        correo="ex@empresa.test",
        rol=ESPECTADOR,
        contrasena=CLAVE,
        ip="203.0.113.4",
    )
    services.editar_usuario(ana.pk, usuario.pk, {"nombre": "Ex Dos"}, ip="203.0.113.4")
    services.cambiar_rol(ana.pk, usuario.pk, TECNICO, ip="203.0.113.4")
    services.desactivar_usuario(ana.pk, usuario.pk, ip="203.0.113.4")
    services.reactivar_usuario(ana.pk, usuario.pk, ip="203.0.113.4")
    services.restablecer_contrasena(ana.pk, usuario.pk, CLAVE_2, ip="203.0.113.4")

    eventos = list(Evento.objects.order_by("id"))
    assert [e.tipo for e in eventos] == [
        "USUARIO_CREADO",
        "USUARIO_EDITADO",
        "USUARIO_ROL_CAMBIADO",
        "USUARIO_DESACTIVADO",
        "USUARIO_REACTIVADO",
        "USUARIO_CONTRASENA_RESTABLECIDA",
    ]
    assert {e.usuario_id for e in eventos} == {ana.pk}
    assert {e.ip for e in eventos} == {"203.0.113.4"}
    assert all("ex@empresa.test" in e.descripcion for e in eventos)  # identifica al afectado


def test_event_payloads_carry_before_and_after(ana, carla):
    services.editar_usuario(ana.pk, carla.pk, {"nombre": "Carla Ruiz", "numero_nomina": "C-9"})
    services.cambiar_rol(ana.pk, carla.pk, ESPECTADOR)
    services.desactivar_usuario(ana.pk, carla.pk)

    editado, rol, desactivado = Evento.objects.order_by("id")
    assert editado.antes == {"nombre": "Carla", "numero_nomina": None}
    assert editado.despues == {"nombre": "Carla Ruiz", "numero_nomina": "C-9"}
    assert (rol.antes, rol.despues) == ({"rol": "TECNICO"}, {"rol": "ESPECTADOR"})
    assert (desactivado.antes, desactivado.despues) == ({"activo": True}, {"activo": False})


def test_the_reset_event_names_actor_and_target_and_carries_no_payload(ana, carla):
    services.restablecer_contrasena(ana.pk, carla.pk, CLAVE_2, ip="198.51.100.9")

    evento = Evento.objects.get()
    assert evento.tipo == "USUARIO_CONTRASENA_RESTABLECIDA"
    assert evento.usuario == ana  # quien actúa
    assert "carla@empresa.test" in evento.descripcion  # el afectado
    assert (evento.antes, evento.despues) == (None, None)
    assert CLAVE_2 not in volcado_eventos()


def test_noop_operations_write_nothing(ana, carla):
    services.editar_usuario(ana.pk, carla.pk, {"nombre": "Carla"})
    services.cambiar_rol(ana.pk, carla.pk, TECNICO)
    services.reactivar_usuario(ana.pk, carla.pk)  # ya está activa
    carla.activo = False
    carla.save()
    services.desactivar_usuario(ana.pk, carla.pk)  # ya está inactiva

    assert Evento.objects.count() == 0


# --- atomicidad: el cambio y su evento, juntos, en ambos sentidos ---


@pytest.fixture
def objetivo_para(carla):
    def preparar(nombre):
        if nombre == "reactivar":
            carla.activo = False
            carla.save()
        return carla

    return preparar


@pytest.mark.parametrize("nombre", sorted(OPERACIONES))
def test_if_the_event_fails_the_change_is_reverted(nombre, ana, objetivo_para):
    objetivo = objetivo_para(nombre)
    antes = instantanea()

    with (
        evento_que_falla(),
        pytest.raises(RuntimeError, match="falla"),
    ):
        OPERACIONES[nombre](ana, objetivo)

    assert instantanea() == antes


@pytest.mark.parametrize("nombre", sorted(OPERACIONES))
def test_if_the_change_fails_after_the_event_no_event_remains(nombre, ana, objetivo_para):
    objetivo = objetivo_para(nombre)
    antes = instantanea()
    real = usuarios.puntos.registrar_evento

    def evento_y_luego_falla(*args, **kwargs):
        real(*args, **kwargs)  # el evento queda insertado...
        raise RuntimeError("el cambio falla después")  # ...y luego algo falla

    with (
        usuarios.sustituir(registrar_evento=evento_y_luego_falla),
        pytest.raises(RuntimeError, match="después"),
    ):
        OPERACIONES[nombre](ana, objetivo)

    assert instantanea() == antes  # ni el cambio ni el evento


@pytest.mark.parametrize("nombre", ["desactivar", "restablecer"])
def test_if_the_operation_fails_no_session_is_closed(nombre, ana, carla):
    """El cierre de sesiones se programa al confirmar: si la operación se revierte, no ocurre."""
    sesion(carla)
    antes = instantanea()

    with (
        evento_que_falla(),
        pytest.raises(RuntimeError),
    ):
        OPERACIONES[nombre](ana, carla)

    assert instantanea() == antes
    assert Session.objects.count() == 1
