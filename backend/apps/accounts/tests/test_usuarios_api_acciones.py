"""API de usuarios, acciones: rol, desactivar, reactivar, restablecer contraseña, sesiones."""

from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session

from apps.accounts.models import Usuario
from apps.audit.models import Evento
from apps.core.testing import CAMBIAR, DEFAULT_PASSWORD, ME, Api, UsuarioFactory

from .ayudantes import ADMIN, BASE, CAMPOS, CLAVE, CLAVE_2, TECNICO, nuevo, reset, ruta, sesion

pytestmark = pytest.mark.django_db


# --- rol, desactivar, reactivar ---


def test_change_role(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test", rol=TECNICO)

    respuesta = api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {"rol": "ESPECTADOR"})

    assert respuesta.status_code == 200
    assert respuesta.json()["rol"] == "ESPECTADOR"
    assert (
        api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {"rol": "SUPERADMIN"}).status_code == 400
    )
    assert api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {}).status_code == 400
    assert api_admin.post(ruta(987654, "cambiar-rol"), {"rol": "ADMIN"}).status_code == 404


def test_an_admin_cannot_change_their_role_deactivate_or_reset_themselves(api_admin, admin):
    clave_antes = admin.password
    rol = api_admin.post(ruta(admin.pk, "cambiar-rol"), {"rol": "TECNICO"})
    desactivar = api_admin.post(ruta(admin.pk, "desactivar"))
    restablecer = api_admin.post(ruta(admin.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2})

    for respuesta in (rol, desactivar, restablecer):
        assert respuesta.status_code == 403
        assert respuesta.json()["codigo"] == "auto_modificacion_no_permitida"
    admin.refresh_from_db()
    assert admin.rol == ADMIN
    assert admin.activo is True
    assert admin.password == clave_antes
    assert admin.check_password(DEFAULT_PASSWORD)
    assert api_admin.get(ME).status_code == 200  # su sesión sigue intacta
    assert Evento.objects.filter(tipo__startswith="USUARIO_").count() == 0


def test_deactivate_and_reactivate(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    desactivado = api_admin.post(ruta(objetivo.pk, "desactivar")).json()
    assert Api().login(objetivo.correo).status_code == 401  # inactivo no entra
    reactivado = api_admin.post(ruta(objetivo.pk, "reactivar")).json()

    assert desactivado["activo"] is False
    assert reactivado["activo"] is True
    assert Api().login(objetivo.correo).status_code == 200
    assert Usuario.objects.filter(pk=objetivo.pk).exists()  # nunca hay borrado físico


def test_there_is_no_delete_and_no_unlock_endpoint(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    assert api_admin.call("DELETE", ruta(objetivo.pk)).status_code == 405
    assert api_admin.call("PUT", ruta(objetivo.pk), nuevo()).status_code == 405
    assert api_admin.post(ruta(objetivo.pk, "desbloquear")).status_code == 404
    assert Usuario.objects.filter(pk=objetivo.pk).exists()


# --- restablecer contraseña ---


def test_reset_sets_the_password_the_admin_wrote_and_returns_no_password(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    respuesta = reset(api_admin, objetivo.pk)

    assert respuesta.status_code == 200
    assert respuesta["Cache-Control"] == "no-store"
    assert set(respuesta.json()) == CAMPOS
    assert CLAVE_2 not in respuesta.content.decode()
    assert Api().login(objetivo.correo, DEFAULT_PASSWORD).status_code == 401
    assert Api().login(objetivo.correo, CLAVE_2).status_code == 200  # entra sin cambio forzado


def test_reset_requires_the_password_and_runs_the_validators(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    sin_clave = api_admin.post(ruta(objetivo.pk, "restablecer-contrasena"), {})
    debil = reset(api_admin, objetivo.pk, "qwertyuiop")
    similar = reset(api_admin, objetivo.pk, "b@empresa.test1")

    assert sin_clave.status_code == 400
    assert debil.status_code == 400
    assert any("muy común" in m for m in debil.json()["campos"]["contrasena"])
    assert similar.status_code == 400
    assert "qwertyuiop" not in debil.content.decode()
    assert Api().login(objetivo.correo, DEFAULT_PASSWORD).status_code == 200  # no cambió


def test_reset_rejects_unknown_fields_and_unknown_users(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    extra = api_admin.post(
        ruta(objetivo.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2, "activo": True}
    )

    assert extra.status_code == 400
    assert reset(api_admin, 987654).status_code == 404


def test_a_body_that_is_not_an_object_is_rejected_with_400(api_admin):
    respuesta = api_admin.post(BASE, ["nombre", "correo"])

    assert respuesta.status_code == 400
    assert Usuario.objects.count() == 1  # solo el administrador de la sesión


def test_an_admin_resets_the_other_admins_password_without_reauthentication(api_admin):
    otro = UsuarioFactory(correo="admin2@empresa.test", rol=ADMIN)
    sesion_otro = sesion(otro)

    respuesta = reset(api_admin, otro.pk)  # solo manda la contraseña nueva del afectado

    assert respuesta.status_code == 200
    assert sesion_otro.get(ME).status_code == 401  # sus sesiones se cerraron
    assert Api().login(otro.correo, CLAVE_2).status_code == 200
    evento = Evento.objects.get(tipo="USUARIO_CONTRASENA_RESTABLECIDA")
    assert evento.usuario.correo == "admin@empresa.test"
    assert "admin2@empresa.test" in evento.descripcion


def test_reset_does_not_unlock_a_locked_account(api_admin, clock):
    """Restablecer NO modifica intentos_fallidos ni bloqueado_hasta."""
    bloqueo = clock.now + timedelta(minutes=10)
    objetivo = UsuarioFactory(
        correo="bloqueada@empresa.test", intentos_fallidos=5, bloqueado_hasta=bloqueo
    )

    reset(api_admin, objetivo.pk)

    objetivo.refresh_from_db()
    assert (objetivo.intentos_fallidos, objetivo.bloqueado_hasta) == (5, bloqueo)
    assert Api().login(objetivo.correo, CLAVE_2).status_code == 401  # sigue bloqueada
    assert api_admin.get(ruta(objetivo.pk)).json()["bloqueado_hasta"] is not None
    clock.advance(minutes=10, seconds=1)
    assert Api().login(objetivo.correo, CLAVE_2).status_code == 200  # se libera solo al vencer


# --- sesiones ---


def test_after_a_role_change_the_open_session_has_the_new_permissions_on_the_next_request(
    api_admin,
):
    otro_admin = UsuarioFactory(correo="admin2@empresa.test", rol=ADMIN)
    sesion_otro = sesion(otro_admin)
    assert sesion_otro.get(BASE).status_code == 200

    api_admin.post(ruta(otro_admin.pk, "cambiar-rol"), {"rol": "ESPECTADOR"})

    denegada = sesion_otro.get(BASE)
    assert denegada.status_code == 403
    assert denegada.json()["codigo"] == "permiso_denegado"
    assert sesion_otro.get(ME).json()["rol"] == "ESPECTADOR"  # la sesión sigue; el rol cambió

    api_admin.post(ruta(otro_admin.pk, "cambiar-rol"), {"rol": "ADMIN"})
    assert sesion_otro.get(BASE).status_code == 200


def test_deactivating_closes_the_open_sessions_immediately(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")
    uno, dos = sesion(objetivo), sesion(objetivo)
    assert uno.get(ME).status_code == 200

    api_admin.post(ruta(objetivo.pk, "desactivar"))

    for abierta in (uno, dos):
        respuesta = abierta.get(ME)
        assert respuesta.status_code == 401
        assert respuesta.json()["codigo"] == "no_autenticado"


def test_resetting_closes_the_open_sessions_immediately(api_admin):
    objetivo = UsuarioFactory(correo="b@empresa.test")
    uno, dos = sesion(objetivo), sesion(objetivo)

    reset(api_admin, objetivo.pk)

    assert uno.get(ME).status_code == 401
    assert dos.get(ME).status_code == 401
    assert not Session.objects.filter(session_key__in=[uno.session_cookie, dos.session_cookie])


# --- flujo completo ---


def test_full_flow_the_admin_assigns_passwords_and_the_technician_cannot_change_hers(api_admin):
    creado = api_admin.post(BASE, nuevo(nombre="Nuevo Técnico", correo="tecnico@empresa.test"))
    assert creado.status_code == 201

    tecnico = Api()
    entrada = tecnico.login("tecnico@empresa.test", CLAVE)  # entra directo: no hay cambio forzado
    assert entrada.status_code == 200
    assert entrada.json()["rol"] == "TECNICO"
    assert tecnico.get(ME).status_code == 200
    assert tecnico.get(BASE).json()["codigo"] == "permiso_denegado"  # no administra

    # No puede cambiar su contraseña por ninguna vía de la API.
    cambio = tecnico.post(CAMBIAR, {"contrasena_actual": CLAVE, "contrasena_nueva": CLAVE_2})
    assert cambio.status_code == 403
    assert cambio.json()["codigo"] == "permiso_denegado"
    assert Api().login("tecnico@empresa.test", CLAVE).status_code == 200  # nada cambió

    # Solo el administrador la cambia, y la sesión del técnico se cierra.
    reset(api_admin, creado.json()["id"])
    assert tecnico.get(ME).status_code == 401
    assert Api().login("tecnico@empresa.test", CLAVE).status_code == 401
    assert Api().login("tecnico@empresa.test", CLAVE_2).status_code == 200
