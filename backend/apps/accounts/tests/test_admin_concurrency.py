"""El login ya no choca con la administración (orden de candados en `apps/accounts/locks.py`).

Sincronización con threading.Event: una operación se deja DETENIDA dentro de su sección crítica
(con L0 y la fila del objetivo ya tomadas) y desde el hilo principal se hace lo que se quiere
probar. No hay pausas por tiempo. Los `Event.wait(timeout)` son topes de seguridad para que una
prueba rota falle en vez de colgarse; el único sondeo (`esperar_bloqueo`) solo confirma que
PostgreSQL ya tiene a un hilo esperando un candado antes de soltar al otro.
"""

import json
import threading
from contextlib import contextmanager

import pytest
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.test import Client

from apps.accounts import locks, services
from apps.accounts.models import Usuario
from apps.accounts.services import autenticacion, usuarios
from apps.audit.models import Evento
from apps.core.testing import (
    CAMBIAR,
    DEFAULT_PASSWORD,
    TOPE,
    Api,
    Retencion,
    UsuarioFactory,
    en_hilo,
    esperar_bloqueo,
    peticion,
)

from .ayudantes import lanza, retener

pytestmark = pytest.mark.django_db(transaction=True)

ADMIN, TECNICO, ESPECTADOR = Usuario.Rol.ADMIN, Usuario.Rol.TECNICO, Usuario.Rol.ESPECTADOR
CLAVE = "tornillo-azul-77-nube"
CLAVE_2 = "nube-de-algodon-88-azul"


# --- herramientas de sincronización -----------------------------------------------------------


def entrar(correo, contrasena=DEFAULT_PASSWORD):
    solicitud = peticion()
    return services.iniciar_sesion(solicitud, correo, contrasena), solicitud


def instantanea():
    usuarios = list(Usuario.objects.order_by("pk").values())
    return json.dumps(usuarios, default=str), Evento.objects.count()


@pytest.fixture
def mundo():
    ana = UsuarioFactory(correo="ana@empresa.test", nombre="Ana", rol=ADMIN)
    beto = UsuarioFactory(correo="beto@empresa.test", nombre="Beto", rol=ADMIN)
    luis = UsuarioFactory(correo="luis@empresa.test", nombre="Luis", rol=TECNICO)
    mara = UsuarioFactory(correo="mara@empresa.test", nombre="Mara", rol=TECNICO, activo=False)
    return {"ana": ana, "beto": beto, "luis": luis, "mara": mara}


# Operaciones de administración de `ana`. Las de la lista "sobre beto" pueden tener a otro
# administrador como objetivo.
SOBRE_OTROS = {
    "crear": lambda m, _: services.crear_usuario(
        m["ana"].pk, nombre="N", correo="n@empresa.test", rol=TECNICO, contrasena=CLAVE
    ),
    "editar": lambda m, a: services.editar_usuario(m["ana"].pk, a.pk, {"nombre": "Otro nombre"}),
    "cambiar_rol": lambda m, a: services.cambiar_rol(m["ana"].pk, a.pk, ESPECTADOR),
    "desactivar": lambda m, a: services.desactivar_usuario(m["ana"].pk, a.pk),
    "reactivar": lambda m, _: services.reactivar_usuario(m["ana"].pk, m["mara"].pk),
    "restablecer": lambda m, a: services.restablecer_contrasena(m["ana"].pk, a.pk, CLAVE_2),
}
SOBRE_UN_ADMINISTRADOR = ["editar", "cambiar_rol", "desactivar", "restablecer"]


# --- 1. el login de un administrador no choca con la administración ---------------------------


@pytest.mark.parametrize("operacion", SOBRE_OTROS)
def test_an_admins_login_is_not_blocked_by_any_admin_operation(operacion, mundo):
    retencion = Retencion()
    with retener("registrar_evento", "operacion", retencion):
        hilo, resultado = en_hilo("operacion", lambda: SOBRE_OTROS[operacion](mundo, mundo["luis"]))
        assert retencion.dentro.wait(TOPE)  # la operación tiene L0 y la fila de su objetivo

        entrada, _ = entrar(mundo["beto"].correo)

        retencion.seguir.set()
        hilo.join(TOPE)

    assert entrada.usuario == mundo["beto"], entrada.motivo
    assert "error" not in resultado, resultado.get("error")


@pytest.mark.parametrize("operacion", SOBRE_UN_ADMINISTRADOR)
def test_the_targets_own_login_is_rejected_as_a_simultaneous_attempt(operacion, mundo):
    retencion = Retencion()
    with retener("registrar_evento", "operacion", retencion):
        hilo, resultado = en_hilo("operacion", lambda: SOBRE_OTROS[operacion](mundo, mundo["beto"]))
        assert retencion.dentro.wait(TOPE)

        entrada, _ = entrar(mundo["beto"].correo)

        retencion.seguir.set()
        hilo.join(TOPE)

    assert entrada.usuario is None
    assert entrada.motivo == services.MotivoFallo.INTENTO_SIMULTANEO
    assert "error" not in resultado, resultado.get("error")
    motivos = Evento.objects.filter(tipo="SESION_FALLIDA").values_list("despues__motivo", flat=True)
    assert list(motivos) == ["intento_simultaneo"]


# --- 2. restablecer contra un login ya verificado en vuelo ------------------------------------


@pytest.mark.parametrize("objetivo", ["beto", "luis"], ids=["administrador", "tecnico"])
def test_a_reset_waits_for_an_inflight_login_and_then_closes_its_session(objetivo, mundo):
    destino = mundo[objetivo]
    real = check_password
    retencion = Retencion()

    def verificar_y_retener(contrasena, encoded):
        correcta = real(contrasena, encoded)
        if correcta and threading.current_thread().name == "login":
            retencion.dentro.set()  # contraseña correcta ya verificada, candado de la fila tomado
            assert retencion.seguir.wait(TOPE)
        return correcta

    solicitud = peticion()
    with autenticacion.sustituir(verificar_hash=verificar_y_retener):
        hilo_login, login = en_hilo(
            "login", lambda: services.iniciar_sesion(solicitud, destino.correo, DEFAULT_PASSWORD)
        )
        assert retencion.dentro.wait(TOPE)
        hilo_reset, reset = en_hilo(
            "reset", lambda: services.restablecer_contrasena(mundo["ana"].pk, destino.pk, CLAVE_2)
        )
        esperar_bloqueo()  # el restablecimiento ya espera la fila que retiene el login
        retencion.seguir.set()
        hilo_login.join(TOPE)
        hilo_reset.join(TOPE)

    assert login["valor"].usuario == destino  # el login en vuelo terminó bien...
    assert "error" not in reset, reset.get("error")
    # ...y el restablecimiento, que fue DESPUÉS, deja inservible la sesión que ese login creó.
    # En el servidor real SessionMiddleware guarda los datos de la sesión DESPUÉS de que el
    # servicio confirme, o sea, tras el restablecimiento: se reproduce ese peor orden.
    solicitud.session.save()
    cliente = Client()
    cliente.cookies["sessionid"] = solicitud.session.session_key
    assert cliente.get("/api/v1/auth/me/").status_code == 401
    destino.refresh_from_db()
    assert destino.check_password(CLAVE_2)
    assert entrar(destino.correo, DEFAULT_PASSWORD)[0].usuario is None


@pytest.mark.parametrize("objetivo", ["beto", "luis"], ids=["administrador", "tecnico"])
def test_a_login_arriving_during_a_reset_is_rejected_and_then_sees_the_new_password(
    objetivo, mundo
):
    destino = mundo[objetivo]
    retencion = Retencion()
    with retener("registrar_evento", "reset", retencion):
        hilo, reset = en_hilo(
            "reset", lambda: services.restablecer_contrasena(mundo["ana"].pk, destino.pk, CLAVE_2)
        )
        assert retencion.dentro.wait(TOPE)

        durante, _ = entrar(destino.correo, DEFAULT_PASSWORD)

        retencion.seguir.set()
        hilo.join(TOPE)

    assert durante.motivo == services.MotivoFallo.INTENTO_SIMULTANEO
    assert "error" not in reset, reset.get("error")
    vieja, _ = entrar(destino.correo, DEFAULT_PASSWORD)
    nueva, _ = entrar(destino.correo, CLAVE_2)
    assert vieja.motivo == services.MotivoFallo.CONTRASENA_INCORRECTA
    assert nueva.usuario == destino


# --- 3. cambiar_contrasena: la versión de la contraseña -----------------------------------------


def test_changing_your_password_returns_409_if_it_changed_after_being_verified(mundo):
    ana, beto = mundo["ana"], mundo["beto"]
    retencion = Retencion()
    solicitud = peticion()
    with retener("cifrar_contrasena", "cambio", retencion, despues=True):
        hilo, cambio = en_hilo(
            "cambio",
            lambda: services.cambiar_contrasena(solicitud, ana.pk, DEFAULT_PASSWORD, CLAVE),
        )
        assert retencion.dentro.wait(TOPE)  # verificada y con su hash nuevo calculado, sin aplicar

        services.restablecer_contrasena(beto.pk, ana.pk, CLAVE_2)  # otro administrador la cambia

        retencion.seguir.set()
        hilo.join(TOPE)

    error = cambio["error"]
    assert isinstance(error, services.ContrasenaModificada)
    assert (error.estado, error.codigo) == (409, "contrasena_modificada")
    ana.refresh_from_db()
    assert ana.check_password(CLAVE_2)  # el cambio del otro administrador no se pisó
    assert not Evento.objects.filter(tipo="USUARIO_CONTRASENA_CAMBIADA").exists()


# --- 4. lock_timeout: «reintenta» y sin estado parcial -----------------------------------------


@contextmanager
def candado_ajeno(tomar):
    """Otra conexión retiene un candado hasta que se la suelta."""
    listo, soltar = threading.Event(), threading.Event()

    def retener_candado():
        with transaction.atomic():
            tomar()
            listo.set()
            soltar.wait(TOPE)

    hilo, _ = en_hilo("ajeno", retener_candado)
    assert listo.wait(TOPE)
    try:
        yield
    finally:
        soltar.set()
        hilo.join(TOPE)


@pytest.mark.parametrize("operacion", SOBRE_OTROS)
def test_when_the_admin_lock_times_out_the_operation_reverts_and_asks_to_retry(
    operacion, mundo, settings
):
    settings.ADMIN_LOCK_TIMEOUT_MS = 200
    antes = instantanea()

    with (
        candado_ajeno(locks.tomar_candado_de_administradores),
        pytest.raises(services.ReintentarMasTarde) as error,
    ):
        SOBRE_OTROS[operacion](mundo, mundo["luis"])

    assert (error.value.estado, error.value.codigo) == (503, "reintentar")
    assert instantanea() == antes  # ni cambios ni eventos


def test_when_the_targets_row_times_out_the_operation_reverts_and_asks_to_retry(mundo, settings):
    settings.ADMIN_LOCK_TIMEOUT_MS = 200
    luis = mundo["luis"]
    antes = instantanea()

    with (
        candado_ajeno(lambda: Usuario.objects.select_for_update().get(pk=luis.pk)),
        pytest.raises(services.ReintentarMasTarde),
    ):
        services.desactivar_usuario(mundo["ana"].pk, luis.pk)

    assert instantanea() == antes


def test_changing_your_own_password_also_times_out_cleanly(mundo, settings):
    settings.ADMIN_LOCK_TIMEOUT_MS = 200
    ana = mundo["ana"]
    antes = instantanea()

    with (
        candado_ajeno(lambda: Usuario.objects.select_for_update().get(pk=ana.pk)),
        pytest.raises(services.ReintentarMasTarde),
    ):
        services.cambiar_contrasena(peticion(), ana.pk, DEFAULT_PASSWORD, CLAVE)

    assert instantanea() == antes
    ana.refresh_from_db()
    assert ana.check_password(DEFAULT_PASSWORD)


# --- 5. revalidación dentro de la sección crítica (estado que cambia tras la lectura previa) ---


def test_changing_your_password_fails_if_you_were_demoted_after_verifying_it(mundo):
    ana, beto = mundo["ana"], mundo["beto"]
    retencion = Retencion()
    with retener("cifrar_contrasena", "cambio", retencion, despues=True):
        hilo, cambio = en_hilo(
            "cambio",
            lambda: services.cambiar_contrasena(peticion(), ana.pk, DEFAULT_PASSWORD, CLAVE),
        )
        assert retencion.dentro.wait(TOPE)

        services.cambiar_rol(beto.pk, ana.pk, TECNICO)  # ya no puede cambiar su contraseña

        retencion.seguir.set()
        hilo.join(TOPE)

    error = cambio["error"]
    assert isinstance(error, services.ActorNoVigente)
    ana.refresh_from_db()
    assert ana.check_password(DEFAULT_PASSWORD)


def test_a_reset_for_admins_only_fails_if_the_target_was_demoted_meanwhile(mundo):
    ana, beto = mundo["ana"], mundo["beto"]
    retencion = Retencion()
    with retener("cifrar_contrasena", "reset", retencion, despues=True):
        hilo, reset = en_hilo(
            "reset",
            lambda: services.restablecer_contrasena(None, beto.pk, CLAVE_2, solo_rol=ADMIN),
        )
        assert retencion.dentro.wait(TOPE)

        services.cambiar_rol(ana.pk, beto.pk, TECNICO)  # el objetivo deja de ser administrador

        retencion.seguir.set()
        hilo.join(TOPE)

    assert isinstance(reset["error"], services.DatosDeUsuarioInvalidos)
    beto.refresh_from_db()
    assert beto.check_password(DEFAULT_PASSWORD)  # no se tocó la contraseña


def test_a_reset_revalidates_the_password_if_the_targets_name_or_email_changed(mundo):
    ana, luis = mundo["ana"], mundo["luis"]
    retencion = Retencion()
    with retener("cifrar_contrasena", "reset", retencion, despues=True):
        hilo, reset = en_hilo(
            "reset", lambda: services.restablecer_contrasena(ana.pk, luis.pk, CLAVE_2)
        )
        assert retencion.dentro.wait(TOPE)

        # La contraseña pasó los validadores con el nombre «Luis»; ahora el nombre se le parece.
        services.editar_usuario(ana.pk, luis.pk, {"nombre": "nube de algodon 88 azul"})

        retencion.seguir.set()
        hilo.join(TOPE)

    assert isinstance(reset["error"], services.DatosDeUsuarioInvalidos)
    luis.refresh_from_db()
    assert luis.check_password(DEFAULT_PASSWORD)


def test_a_reset_keeps_the_hash_if_the_changed_name_does_not_matter(mundo):
    ana, luis = mundo["ana"], mundo["luis"]
    retencion = Retencion()
    with retener("cifrar_contrasena", "reset", retencion, despues=True):
        hilo, reset = en_hilo(
            "reset", lambda: services.restablecer_contrasena(ana.pk, luis.pk, CLAVE_2)
        )
        assert retencion.dentro.wait(TOPE)

        services.editar_usuario(ana.pk, luis.pk, {"nombre": "Luis Alberto"})

        retencion.seguir.set()
        hilo.join(TOPE)

    assert "error" not in reset, reset.get("error")
    luis.refresh_from_db()
    assert luis.check_password(CLAVE_2)


# --- 6. otros errores de base de datos NO se disfrazan de «reintenta» ---------------------------


@pytest.mark.parametrize("sqlstate", ["40P01", "57014", "08006"])
def test_other_database_errors_propagate_instead_of_asking_to_retry(sqlstate, mundo):
    from django.db import OperationalError

    class Causa(Exception):
        pass

    causa = Causa()
    causa.sqlstate = sqlstate
    error = OperationalError("fallo de base de datos")
    error.__cause__ = causa

    with (
        usuarios.sustituir(tomar_candado_de_administradores=lanza(error)),
        pytest.raises(OperationalError, match="fallo de base de datos"),
    ):
        services.desactivar_usuario(mundo["ana"].pk, mundo["luis"].pk)


def test_the_change_password_view_answers_a_lock_timeout_with_503(usuario, relaxed_rate, settings):
    settings.ADMIN_LOCK_TIMEOUT_MS = 200
    api = Api()
    assert api.login(usuario.correo).status_code == 200

    with candado_ajeno(lambda: Usuario.objects.select_for_update().get(pk=usuario.pk)):
        respuesta = api.post(
            CAMBIAR, {"contrasena_actual": DEFAULT_PASSWORD, "contrasena_nueva": CLAVE}
        )

    assert respuesta.status_code == 503
    assert respuesta.json()["codigo"] == "reintentar"
