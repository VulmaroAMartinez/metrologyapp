"""`sesion_version`: las sesiones anteriores no reviven, con o sin borrado de filas.

La versión entra en el hash de sesión y sube (UPDATE atómico con F) al desactivar y al restablecer
la contraseña, bajo los mismos candados. El borrado de sesiones (on_commit) es solo limpieza: con
él apagado o fallando, una sesión anterior sigue recibiendo 401 aunque se reactive al usuario o se
restablezca la MISMA contraseña.
"""

import json
import logging
import threading

import pytest
from django.contrib.sessions.models import Session
from django.db import transaction
from django.test import Client

from apps.accounts import services
from apps.accounts.models import Usuario
from apps.audit.models import Evento, TipoEvento
from apps.audit.sanitize import DatosEventoInvalidosError, sanear, solo_permitidos
from apps.audit.services import registrar_evento
from apps.core.testing import DEFAULT_PASSWORD, ME, Api, UsuarioFactory, en_hilo, peticion

from .ayudantes import evento_que_falla, limpieza_que_falla, sin_limpieza

pytestmark = pytest.mark.django_db(transaction=True)

BASE = "/api/v1/usuarios/"
CLAVE_2 = "nube-de-algodon-88-azul"
LIMPIEZAS = pytest.mark.parametrize(
    "limpieza", [sin_limpieza, limpieza_que_falla], ids=["apagada", "falla"]
)


def ruta(pk, accion):
    return f"{BASE}{pk}/{accion}/"


def abrir(usuario):
    api = Api()
    assert api.login(usuario.correo).status_code == 200
    return api


def version(usuario) -> int:
    return Usuario.objects.get(pk=usuario.pk).sesion_version


# --- las sesiones anteriores no reviven -----------------------------------------------------------


@LIMPIEZAS
def test_a_session_from_before_deactivating_and_reactivating_never_revives(
    limpieza, api_ana, tecnico
):
    vieja = abrir(tecnico)  # nadie la usa mientras el usuario está desactivado

    with limpieza():
        assert api_ana.post(ruta(tecnico.pk, "desactivar")).status_code == 200
        assert api_ana.post(ruta(tecnico.pk, "reactivar")).status_code == 200

    assert vieja.get(ME).status_code == 401
    nueva = abrir(tecnico)  # una sesión creada DESPUÉS es válida...
    assert nueva.get(ME).status_code == 200
    assert vieja.get(ME).status_code == 401  # ...y la vieja sigue muerta


@LIMPIEZAS
def test_a_session_from_before_a_reset_gets_401_even_if_the_new_password_is_the_same(
    limpieza, api_ana, tecnico
):
    vieja = abrir(tecnico)

    with limpieza():
        respuesta = api_ana.post(
            ruta(tecnico.pk, "restablecer-contrasena"), {"contrasena": DEFAULT_PASSWORD}
        )

    assert respuesta.status_code == 200
    assert vieja.get(ME).status_code == 401  # el hash de la contraseña no cambió; la versión sí
    assert abrir(tecnico).get(ME).status_code == 200  # y la contraseña sigue sirviendo


@LIMPIEZAS
def test_a_session_created_after_a_reset_stays_valid(limpieza, api_ana, tecnico):
    with limpieza():
        api_ana.post(ruta(tecnico.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2})
    nueva = Api()
    assert nueva.login(tecnico.correo, CLAVE_2).status_code == 200

    api_ana.post(ruta(tecnico.pk, "reactivar"))  # operaciones que NO suben la versión
    api_ana.call("PATCH", f"{BASE}{tecnico.pk}/", {"nombre": "Otro Nombre"})
    api_ana.post(ruta(tecnico.pk, "cambiar-rol"), {"rol": "ESPECTADOR"})

    assert nueva.get(ME).status_code == 200


def test_the_session_of_a_login_that_finished_before_the_deactivation_is_dead(usuario, tecnico):
    """`SessionMiddleware` guarda los datos de la sesión DESPUÉS de que el servicio confirma: se
    reproduce el peor orden (login, desactivar, reactivar, y solo entonces se guarda la sesión)."""
    solicitud = peticion()
    assert services.iniciar_sesion(solicitud, tecnico.correo, DEFAULT_PASSWORD).usuario == tecnico

    with sin_limpieza():
        services.desactivar_usuario(usuario.pk, tecnico.pk)
        services.reactivar_usuario(usuario.pk, tecnico.pk)
    solicitud.session.save()
    cliente = Client()
    cliente.cookies["sessionid"] = solicitud.session.session_key

    assert cliente.get(ME).status_code == 401


def test_the_cleanup_that_does_work_still_deletes_the_rows(api_ana, tecnico):
    vieja = abrir(tecnico)

    api_ana.post(ruta(tecnico.pk, "desactivar"))

    assert not Session.objects.filter(session_key=vieja.session_cookie).exists()


# --- el contador ---------------------------------------------------------------------------------


def test_the_version_goes_up_once_per_deactivation_and_per_reset_and_never_otherwise(
    usuario, tecnico
):
    assert version(tecnico) == 0

    services.desactivar_usuario(usuario.pk, tecnico.pk)
    assert version(tecnico) == 1
    services.desactivar_usuario(usuario.pk, tecnico.pk)  # ya estaba desactivado: sin cambios
    assert version(tecnico) == 1
    services.reactivar_usuario(usuario.pk, tecnico.pk)
    services.cambiar_rol(usuario.pk, tecnico.pk, "ESPECTADOR")
    services.editar_usuario(usuario.pk, tecnico.pk, {"nombre": "Otro"})
    assert version(tecnico) == 1
    services.restablecer_contrasena(usuario.pk, tecnico.pk, CLAVE_2)
    assert version(tecnico) == 2


def test_a_failed_operation_does_not_bump_the_version(usuario, tecnico):
    with (
        evento_que_falla(),
        pytest.raises(RuntimeError),
    ):
        services.desactivar_usuario(usuario.pk, tecnico.pk)

    assert version(tecnico) == 0  # se revirtió junto con todo lo demás


def test_concurrent_resets_never_lose_an_increment(usuario, tecnico):
    beto = UsuarioFactory(correo="beto@empresa.test", rol="ADMIN")
    salida = threading.Barrier(2)

    def restablecer(actor, clave):
        def correr():
            salida.wait(10)
            return services.restablecer_contrasena(actor.pk, tecnico.pk, clave)

        return correr

    h1, r1 = en_hilo("uno", restablecer(usuario, CLAVE_2))
    h2, r2 = en_hilo("dos", restablecer(beto, "tornillo-azul-77-nube"))
    h1.join(10)
    h2.join(10)

    assert "error" not in r1, r1.get("error")
    assert "error" not in r2, r2.get("error")
    assert version(tecnico) == 2


# --- el hash de sesión --------------------------------------------------------------------------


def usuario_en_memoria(**campos):
    return Usuario(correo="a@e.test", nombre="A", rol="ADMIN", password="hash-x", **campos)


def test_the_session_hash_depends_on_the_password_and_on_the_version():
    base = usuario_en_memoria(sesion_version=0).get_session_auth_hash()

    assert usuario_en_memoria(sesion_version=0).get_session_auth_hash() == base
    assert usuario_en_memoria(sesion_version=1).get_session_auth_hash() != base
    otra = usuario_en_memoria(sesion_version=0)
    otra.password = "hash-y"
    assert otra.get_session_auth_hash() != base


def test_the_fallback_hashes_include_the_version_too(settings):
    settings.SECRET_KEY_FALLBACKS = ["clave-antigua-" + "x" * 40]
    cero = usuario_en_memoria(sesion_version=0)
    uno = usuario_en_memoria(sesion_version=1)

    assert list(cero.get_session_auth_fallback_hash()) != list(uno.get_session_auth_fallback_hash())
    assert next(iter(cero.get_session_auth_fallback_hash())) != cero.get_session_auth_hash()


# --- nunca se expone ----------------------------------------------------------------------------


def test_the_session_version_is_in_no_response_event_or_log(api_ana, tecnico, caplog):
    caplog.set_level(logging.DEBUG)
    respuestas = [
        api_ana.post(ruta(tecnico.pk, "desactivar")),
        api_ana.post(ruta(tecnico.pk, "reactivar")),
        api_ana.post(ruta(tecnico.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2}),
        api_ana.get(ME),
        api_ana.get(BASE),
        api_ana.get(f"{BASE}{tecnico.pk}/"),
        api_ana.get("/api/v1/bitacora/eventos/?tamano=100"),
        Api().login(tecnico.correo, CLAVE_2),
    ]

    for respuesta in respuestas:
        assert "sesion_version" not in respuesta.content.decode()
    eventos = json.dumps(list(Evento.objects.values()), default=str)
    assert "sesion_version" not in eventos
    assert "sesion_version" not in caplog.text


def test_sanitize_rejects_the_session_version_and_the_event_is_not_written():
    with pytest.raises(DatosEventoInvalidosError, match="sesion_version"):
        sanear({"sesion_version": 3}, "despues")
    antes = Evento.objects.count()

    with pytest.raises(DatosEventoInvalidosError), transaction.atomic():
        registrar_evento(
            TipoEvento.USUARIO_EDITADO, None, "x", despues={"rol": "ADMIN", "sesion_version": 1}
        )

    assert Evento.objects.count() == antes
    assert solo_permitidos({"rol": "ADMIN", "sesion_version": 3}) == {"rol": "ADMIN"}
