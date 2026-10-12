"""Servicios de usuarios: reglas de administradores y restablecimiento por el Sistema."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Usuario
from apps.audit.models import Evento
from apps.core.testing import DEFAULT_PASSWORD, UsuarioFactory

from .ayudantes import ADMIN, CLAVE_2, OPERACIONES, TECNICO, instantanea, sesion

pytestmark = pytest.mark.django_db


# --- reglas de administradores ---


def test_an_admin_cannot_deactivate_change_role_or_reset_their_own_password(ana, beto):
    clave_antes = ana.password
    for operacion in (
        lambda: services.desactivar_usuario(ana.pk, ana.pk),
        lambda: services.cambiar_rol(ana.pk, ana.pk, TECNICO),
        lambda: services.restablecer_contrasena(ana.pk, ana.pk, CLAVE_2),
    ):
        with pytest.raises(services.AutoModificacionNoPermitida) as error:
            operacion()
        assert error.value.codigo == "auto_modificacion_no_permitida"
        assert error.value.estado == 403

    ana.refresh_from_db()
    assert ana.activo is True
    assert ana.rol == ADMIN
    assert ana.password == clave_antes
    assert Evento.objects.count() == 0


def test_an_admin_can_edit_their_own_data(ana):
    services.editar_usuario(ana.pk, ana.pk, {"nombre": "Ana María"})

    ana.refresh_from_db()
    assert ana.nombre == "Ana María"


def test_an_admin_can_reset_the_other_admins_password(ana, beto):
    sesion(beto)

    services.restablecer_contrasena(ana.pk, beto.pk, CLAVE_2)

    beto.refresh_from_db()
    assert beto.check_password(CLAVE_2)
    assert not beto.check_password(DEFAULT_PASSWORD)


def test_an_admin_can_demote_and_deactivate_another_admin(ana, beto):
    services.cambiar_rol(ana.pk, beto.pk, TECNICO)
    beto.rol = ADMIN
    beto.save()
    services.desactivar_usuario(ana.pk, beto.pk)

    beto.refresh_from_db()
    assert beto.activo is False


def test_the_last_active_admin_invariant(ana, beto):
    with pytest.raises(services.UltimoAdministrador) as error:
        services.usuarios._garantizar_administrador_restante([ana], ana.pk)
    assert error.value.codigo == "ultimo_administrador"
    assert error.value.estado == 409

    services.usuarios._garantizar_administrador_restante([ana, beto], ana.pk)  # queda beto


@pytest.mark.parametrize("operacion", sorted(OPERACIONES))
@pytest.mark.parametrize("motivo", ["tecnico", "inactivo", "inexistente"])
def test_an_actor_who_is_no_longer_an_active_admin_is_refused(operacion, motivo, ana, carla):
    actor = UsuarioFactory(correo="exadmin@empresa.test", rol=ADMIN)
    if motivo == "tecnico":
        Usuario.objects.filter(pk=actor.pk).update(rol=TECNICO)
    elif motivo == "inactivo":
        Usuario.objects.filter(pk=actor.pk).update(activo=False)
    else:
        actor.pk = 987654
    antes = instantanea()

    with pytest.raises(services.ActorNoVigente) as error:
        OPERACIONES[operacion](actor, carla)

    assert error.value.estado == 403
    assert error.value.codigo == "permiso_denegado"
    assert instantanea() == antes


@pytest.mark.parametrize(
    "operacion", ["editar", "cambiar_rol", "desactivar", "reactivar", "restablecer"]
)
def test_unknown_target_is_404(operacion, ana):
    objetivo = Usuario(pk=987654)

    with pytest.raises(services.UsuarioInexistente) as error:
        OPERACIONES[operacion](ana, objetivo)

    assert error.value.estado == 404


# --- el restablecimiento por el Sistema (comando de servidor) ---


def test_the_system_can_reset_an_admin_without_an_actor(ana):
    services.restablecer_contrasena(None, ana.pk, CLAVE_2, solo_rol=ADMIN)

    ana.refresh_from_db()
    assert ana.check_password(CLAVE_2)
    evento = Evento.objects.get()
    assert evento.tipo == "USUARIO_CONTRASENA_RESTABLECIDA"
    assert evento.usuario is None
    assert "por comando de servidor" in evento.descripcion
    assert "ana@empresa.test" in evento.descripcion


def test_the_system_reset_refuses_non_admins(carla):
    antes = instantanea()

    with pytest.raises(services.DatosDeUsuarioInvalidos, match="no es administrador"):
        services.restablecer_contrasena(None, carla.pk, CLAVE_2, solo_rol=ADMIN)

    assert instantanea() == antes


def test_the_system_reset_works_even_for_the_only_admin_and_does_not_reactivate(ana):
    Usuario.objects.filter(pk=ana.pk).update(activo=False)

    services.restablecer_contrasena(None, ana.pk, CLAVE_2, solo_rol=ADMIN)

    ana.refresh_from_db()
    assert ana.check_password(CLAVE_2)
    assert ana.activo is False


# --- restablecer / reactivar / desactivar no son un desbloqueo ---


def test_resetting_the_password_does_not_touch_the_lock_counters(ana):
    bloqueo = timezone.now() + timedelta(minutes=10)
    bloqueada = UsuarioFactory(
        correo="bloqueada@empresa.test", intentos_fallidos=5, bloqueado_hasta=bloqueo
    )

    services.restablecer_contrasena(ana.pk, bloqueada.pk, CLAVE_2)

    bloqueada.refresh_from_db()
    assert bloqueada.intentos_fallidos == 5  # sigue igual
    assert bloqueada.bloqueado_hasta == bloqueo  # sigue bloqueada: no es un desbloqueo manual
    assert bloqueada.check_password(CLAVE_2)


def test_the_system_reset_does_not_touch_the_lock_counters_either(ana):
    bloqueo = timezone.now() + timedelta(minutes=10)
    Usuario.objects.filter(pk=ana.pk).update(intentos_fallidos=5, bloqueado_hasta=bloqueo)

    services.restablecer_contrasena(None, ana.pk, CLAVE_2, solo_rol=ADMIN)

    ana.refresh_from_db()
    assert (ana.intentos_fallidos, ana.bloqueado_hasta) == (5, bloqueo)


def test_deactivating_and_reactivating_do_not_touch_the_lock_counters(ana):
    bloqueo = timezone.now() + timedelta(minutes=10)
    usuario = UsuarioFactory(correo="b@empresa.test", intentos_fallidos=5, bloqueado_hasta=bloqueo)

    services.desactivar_usuario(ana.pk, usuario.pk)
    services.reactivar_usuario(ana.pk, usuario.pk)

    usuario.refresh_from_db()
    assert (usuario.intentos_fallidos, usuario.bloqueado_hasta) == (5, bloqueo)


def test_there_is_no_manual_unlock_in_the_services():
    nombres = {n.lower() for n in dir(services)}

    assert not [n for n in nombres if "desbloque" in n or "unlock" in n]
