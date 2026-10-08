import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Usuario
from config.settings import base

from .conftest import LOGIN, ME, Api
from .factories import DEFAULT_PASSWORD, UsuarioFactory

pytestmark = pytest.mark.django_db

CORREO = "ana@empresa.test"


@pytest.fixture
def cuentas(db):
    """Un caso por cada razón por la que un login puede fallar."""
    UsuarioFactory(correo="activo@empresa.test")
    UsuarioFactory(correo="inactivo@empresa.test", activo=False)
    UsuarioFactory(
        correo="bloqueado@empresa.test", bloqueado_hasta=timezone.now() + timedelta(minutes=10)
    )
    sin_clave = UsuarioFactory(correo="sinclave@empresa.test")
    sin_clave.set_unusable_password()
    sin_clave.save()


CASOS = {
    "correo inexistente": ("nadie@empresa.test", DEFAULT_PASSWORD),
    "contraseña incorrecta": ("activo@empresa.test", "otra-contrasena-1"),
    "usuario inactivo": ("inactivo@empresa.test", DEFAULT_PASSWORD),
    "cuenta bloqueada (contraseña correcta)": ("bloqueado@empresa.test", DEFAULT_PASSWORD),
    "sin contraseña utilizable": ("sinclave@empresa.test", DEFAULT_PASSWORD),
}


# --- respuesta idéntica ------------------------------------------------------------------


def test_every_failure_has_the_same_status_body_and_headers(cuentas, relaxed_rate):
    respuestas = {}
    for nombre, (correo, contrasena) in CASOS.items():
        api = Api()
        respuestas[nombre] = api.login(correo, contrasena)
    referencia = respuestas["correo inexistente"]

    for nombre, respuesta in respuestas.items():
        assert respuesta.status_code == 401, nombre
        assert respuesta.content == referencia.content, nombre
        assert dict(respuesta.headers) == dict(referencia.headers), nombre
        assert "sessionid" not in respuesta.cookies, nombre
    assert referencia.json() == {
        "codigo": "credenciales_invalidas",
        "detalle": "Correo o contraseña incorrectos.",
    }
    assert Session.objects.count() == 0


def test_a_hash_is_verified_exactly_once_in_every_case(cuentas, relaxed_rate):
    for nombre, (correo, contrasena) in CASOS.items():
        with mock.patch.object(services, "check_password", wraps=services.check_password) as spy:
            Api().login(correo, contrasena)

        assert spy.call_count == 1, nombre
        encoded = spy.call_args.args[1]
        if nombre not in {"contraseña incorrecta"}:
            assert encoded == services.dummy_hash(), nombre  # verifica contra el hash ficticio
        else:
            assert encoded != services.dummy_hash(), nombre


def test_dummy_hash_is_computed_once_per_process():
    assert services.dummy_hash() is services.dummy_hash()


def test_real_argon2_end_to_end_including_the_dummy_hash(relaxed_rate):
    services.dummy_hash.cache_clear()
    try:
        with override_settings(PASSWORD_HASHERS=base.PASSWORD_HASHERS):
            UsuarioFactory(correo=CORREO)
            assert services.dummy_hash().startswith("argon2$argon2id$")
            assert Api().login(CORREO, "mal").status_code == 401
            assert Api().login("nadie@empresa.test", "mal").status_code == 401
            assert Api().login(CORREO, DEFAULT_PASSWORD).status_code == 200
    finally:
        services.dummy_hash.cache_clear()


# --- bloqueo ------------------------------------------------------------------------------


def test_locks_on_the_exact_attempt_and_unlocks_when_it_expires(api, clock, relaxed_rate, usuario):
    for intento in range(1, 5):
        assert api.login(usuario.correo, "mal").status_code == 401
        usuario.refresh_from_db()
        assert (usuario.intentos_fallidos, usuario.bloqueado_hasta) == (intento, None)

    assert api.login(usuario.correo, "mal").status_code == 401  # el 5.º
    usuario.refresh_from_db()
    bloqueo = clock.now + timedelta(minutes=15)
    assert (usuario.intentos_fallidos, usuario.bloqueado_hasta) == (5, bloqueo)

    # Mientras dura el bloqueo, ni la contraseña correcta entra.
    assert api.login(usuario.correo, DEFAULT_PASSWORD).status_code == 401
    clock.advance(minutes=14, seconds=59)
    assert api.login(usuario.correo, DEFAULT_PASSWORD).status_code == 401

    clock.advance(seconds=1)  # exactamente bloqueado_hasta
    assert api.login(usuario.correo, DEFAULT_PASSWORD).status_code == 200
    usuario.refresh_from_db()
    assert (usuario.intentos_fallidos, usuario.bloqueado_hasta) == (0, None)


def test_a_failure_after_an_expired_lock_starts_a_new_cycle(api, clock, relaxed_rate, usuario):
    for _ in range(5):
        api.login(usuario.correo, "mal")
    clock.advance(minutes=16)

    api.login(usuario.correo, "mal")

    usuario.refresh_from_db()
    assert (usuario.intentos_fallidos, usuario.bloqueado_hasta) == (1, None)
    assert api.login(usuario.correo, DEFAULT_PASSWORD).status_code == 200  # no queda bloqueada


def test_failures_while_locked_do_not_extend_the_lock(api, clock, relaxed_rate, usuario):
    for _ in range(5):
        api.login(usuario.correo, "mal")
    usuario.refresh_from_db()
    original = usuario.bloqueado_hasta

    clock.advance(minutes=5)
    api.login(usuario.correo, "mal")

    usuario.refresh_from_db()
    assert usuario.bloqueado_hasta == original
    assert usuario.intentos_fallidos == 5


def test_successful_login_resets_the_counter(api, relaxed_rate, usuario):
    for _ in range(3):
        api.login(usuario.correo, "mal")
    usuario.refresh_from_db()
    assert usuario.intentos_fallidos == 3

    assert api.login(usuario.correo, DEFAULT_PASSWORD).status_code == 200

    usuario.refresh_from_db()
    assert usuario.intentos_fallidos == 0


def test_lock_threshold_is_configurable(api, relaxed_rate, usuario, settings):
    settings.LOGIN_MAX_FAILED_ATTEMPTS = 2
    settings.LOGIN_LOCKOUT_MINUTES = 1

    api.login(usuario.correo, "mal")
    api.login(usuario.correo, "mal")

    usuario.refresh_from_db()
    assert usuario.bloqueado_hasta is not None


def test_failure_counter_is_atomic_with_stale_instances(usuario):
    # Dos "workers" leyeron el contador en 0; un leer-modificar-guardar dejaría 1.
    uno = Usuario.objects.get(pk=usuario.pk)
    otro = Usuario.objects.get(pk=usuario.pk)

    services._registrar_fallo(uno.pk, timezone.now())
    services._registrar_fallo(otro.pk, timezone.now())

    usuario.refresh_from_db()
    assert usuario.intentos_fallidos == 2


@pytest.mark.django_db(transaction=True)
def test_failure_counter_is_atomic_across_connections(settings):
    settings.LOGIN_MAX_FAILED_ATTEMPTS = 1000
    usuario = UsuarioFactory()

    def fallar(_):
        try:
            services._registrar_fallo(usuario.pk, timezone.now())
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(fallar, range(30)))

    usuario.refresh_from_db()
    assert usuario.intentos_fallidos == 30


@pytest.mark.django_db(transaction=True)
def test_parallel_guesses_cannot_exceed_the_lock_threshold_and_never_skip_the_hash():
    """Ráfaga de 12 intentos simultáneos contra la misma cuenta (umbral: 5).

    La verificación se alarga 0,2 s para que todos coincidan. Con lectura sin candado, los 12
    probarían la contraseña real (el umbral no serviría). Con NOWAIT, solo quien obtiene el
    candado la prueba; los demás se rechazan sin esperar, pero cada uno ejecuta exactamente una
    verificación de hash (contra el ficticio), así el tiempo no revela que la cuenta existe.
    """
    usuario = UsuarioFactory(correo=CORREO)
    llamadas = []
    real = services.check_password

    def lenta(contrasena, encoded):
        time.sleep(0.2)
        llamadas.append(encoded)
        return real(contrasena, encoded)

    def intento(_):
        try:
            return services.iniciar_sesion(None, CORREO, "mal-intento-1").motivo
        finally:
            connection.close()

    with mock.patch.object(services, "check_password", side_effect=lenta):
        with ThreadPoolExecutor(max_workers=12) as pool:
            motivos = list(pool.map(intento, range(12)))

    usuario.refresh_from_db()
    contra_real = [e for e in llamadas if e == usuario.password]
    assert len(llamadas) == 12  # una verificación por intento, nunca ninguna
    assert 1 <= len(contra_real) <= 5  # jamás más que el umbral
    assert motivos.count(services.MotivoFallo.CONTRASENA_INCORRECTA) == len(contra_real)
    assert motivos.count(services.MotivoFallo.INTENTO_SIMULTANEO) >= 1
    assert all(m is not None for m in motivos)


def test_contended_attempt_is_rejected_without_waiting_and_looks_like_any_failure(api, usuario):
    from django.db import OperationalError
    from psycopg.errors import LockNotAvailable

    error = OperationalError("could not obtain lock")
    error.__cause__ = LockNotAvailable()
    with (
        mock.patch.object(services.Usuario.objects, "select_for_update", side_effect=error),
        mock.patch.object(services, "check_password", wraps=services.check_password) as spy,
    ):
        respuesta = api.login(usuario.correo, DEFAULT_PASSWORD)  # incluso con la contraseña buena

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "credenciales_invalidas"
    assert spy.call_count == 1
    assert spy.call_args.args[1] == services.dummy_hash()


def test_other_database_errors_are_not_swallowed(api, usuario):
    from django.db import OperationalError

    with (
        mock.patch.object(
            services.Usuario.objects, "select_for_update", side_effect=OperationalError("caída")
        ),
        pytest.raises(OperationalError, match="caída"),
    ):
        services.iniciar_sesion(None, usuario.correo, DEFAULT_PASSWORD)


# --- límite de tasa ---------------------------------------------------------------------


def test_sixth_attempt_in_a_minute_is_429_with_retry_after_and_computes_no_hash(
    api, clock, usuario
):
    with mock.patch.object(services, "check_password", wraps=services.check_password) as spy:
        estados = [api.login(usuario.correo, "mal").status_code for _ in range(5)]
        sexto = api.login(usuario.correo, DEFAULT_PASSWORD)  # ni siquiera la correcta

    assert estados == [401] * 5
    assert sexto.status_code == 429
    assert sexto["Retry-After"] == "30"  # el reloj está en el segundo 30 del minuto
    assert sexto.json()["codigo"] == "demasiados_intentos"
    assert spy.call_count == 5  # el 429 se decide antes de calcular cualquier hash


def test_limit_applies_to_unknown_emails_too(api, clock):
    estados = [api.login("nadie@empresa.test", "mal").status_code for _ in range(6)]

    assert estados == [401] * 5 + [429]  # no revela si el correo existe


def test_limit_resets_with_the_next_window(api, clock, usuario, settings):
    settings.LOGIN_MAX_FAILED_ATTEMPTS = 1000  # aquí se prueba la ventana, no el bloqueo
    for _ in range(6):
        api.login(usuario.correo, "mal")
    assert api.login(usuario.correo, "mal").status_code == 429

    clock.advance(seconds=31)

    assert api.login(usuario.correo, DEFAULT_PASSWORD).status_code == 200


def test_ip_and_email_limit_does_not_block_other_emails_or_other_ips(api, clock):
    for _ in range(6):
        api.login("a@empresa.test", "mal")

    assert api.login("b@empresa.test", "mal").status_code == 401  # otro correo, misma IP
    otra_ip = Api()
    assert otra_ip.login("a@empresa.test", "mal", REMOTE_ADDR="198.51.100.9").status_code == 401


def test_ip_only_limit_stops_a_sweep_over_many_emails(api, clock, settings):
    settings.LOGIN_RATE_PER_IP = 3

    estados = [api.login(f"u{n}@empresa.test", "mal").status_code for n in range(5)]

    assert estados == [401, 401, 401, 429, 429]


def test_forged_forwarded_headers_from_an_untrusted_source_do_not_evade_the_limit(clock):
    estados = []
    for n in range(7):
        atacante = Api()
        estados.append(
            atacante.login(
                CORREO,
                "mal",
                REMOTE_ADDR="203.0.113.50",
                HTTP_X_FORWARDED_FOR=f"10.0.0.{n}",
                HTTP_X_REAL_IP=f"10.1.0.{n}",
            ).status_code
        )

    assert estados == [401] * 5 + [429] * 2


def test_trusted_proxy_forwarded_for_identifies_the_real_client(clock):
    caddy = {"REMOTE_ADDR": "172.18.0.5"}  # Caddy, dentro de la red interna
    cliente_a = [
        Api().login(CORREO, "mal", **caddy, HTTP_X_FORWARDED_FOR="198.51.100.1").status_code
        for _ in range(6)
    ]
    cliente_b = Api().login(CORREO, "mal", **caddy, HTTP_X_FORWARDED_FOR="198.51.100.2")

    assert cliente_a == [401] * 5 + [429]
    assert cliente_b.status_code == 401  # otra IP real: su propio contador


# --- validación de entrada y datos sensibles ------------------------------------------------


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"correo": CORREO},
        {"contrasena": "x"},
        {"correo": "no-es-correo", "contrasena": "x"},
        {"correo": CORREO, "contrasena": "x" * 257},
    ],
)
def test_malformed_login_is_400(api, cuerpo):
    assert api.post(LOGIN, cuerpo).status_code == 400


def test_login_is_case_insensitive_on_email_and_trims_spaces(api, usuario):
    assert api.login("  ANA@Empresa.TEST ").status_code == 200
    assert api.get(ME).json()["correo"] == usuario.correo


def test_no_password_hash_or_email_reaches_the_logs(api, caplog, usuario, relaxed_rate):
    caplog.set_level(logging.DEBUG)
    secretos = ["mal-password-9", "otro-mal-password-8", DEFAULT_PASSWORD, usuario.password]

    api.login(usuario.correo, secretos[0])
    api.login("nadie@empresa.test", secretos[1])
    for _ in range(5):
        api.login(usuario.correo, secretos[0])  # llega al bloqueo
    api.login(usuario.correo, DEFAULT_PASSWORD)

    assert "motivo=" in caplog.text  # sí se registra el motivo interno
    for secreto in secretos:
        assert secreto not in caplog.text
    assert usuario.correo not in caplog.text
    assert "nadie@empresa.test" not in caplog.text
