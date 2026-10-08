"""Reglas de negocio de la autenticación. Las vistas solo validan forma y delegan aquí.

Puntos de enganche para el commit 6 (eventos, en la misma transacción que el cambio):
buscar `# HOOK evento:`. Nunca se registran contraseñas ni hashes.
"""

import functools
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from django.conf import settings
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import Case, DateTimeField, F, Q, Value, When
from django.db.models.lookups import GreaterThanOrEqual
from django.utils import timezone

from .models import Usuario

logger = logging.getLogger(__name__)

SESSION_BACKEND = "django.contrib.auth.backends.ModelBackend"


class MotivoFallo(StrEnum):
    """Solo para uso interno (logs y eventos). Nunca se muestra al cliente."""

    NO_EXISTE = "no_existe"
    INACTIVO = "inactivo"
    BLOQUEADO = "bloqueado"
    SIN_CONTRASENA = "sin_contrasena"
    CONTRASENA_INCORRECTA = "contrasena_incorrecta"


@dataclass(frozen=True)
class ResultadoLogin:
    usuario: Usuario | None
    motivo: MotivoFallo | None


class ContrasenaActualIncorrecta(Exception):  # noqa: N818
    pass


class ContrasenaRepetida(Exception):  # noqa: N818
    pass


@functools.cache
def dummy_hash() -> str:
    """Hash ficticio con los mismos parámetros que los reales, calculado una vez por proceso
    (AccountsConfig.ready lo precalcula) para que verificar contra él cueste lo mismo."""
    return make_password(secrets.token_urlsafe(32))


def _motivo_no_elegible(usuario, ahora) -> MotivoFallo | None:
    if usuario is None:
        return MotivoFallo.NO_EXISTE
    if not usuario.activo:
        return MotivoFallo.INACTIVO
    if usuario.bloqueado_hasta is not None and usuario.bloqueado_hasta > ahora:
        return MotivoFallo.BLOQUEADO
    if not usuario.has_usable_password():
        return MotivoFallo.SIN_CONTRASENA
    return None


def iniciar_sesion(request, correo: str, contrasena: str) -> ResultadoLogin:
    # La fila queda bloqueada (FOR UPDATE) desde que se lee hasta que se registra el resultado:
    # los intentos simultáneos contra la misma cuenta se serializan y cada uno ve el bloqueo
    # que dejó el anterior. Sin esto, una ráfaga en paralelo pasaría la comprobación de
    # "no bloqueado" antes de que se registrara ningún fallo y superaría el umbral.
    with transaction.atomic():
        usuario = Usuario.objects.select_for_update().filter(correo=correo.strip().lower()).first()
        ahora = timezone.now()  # después de obtener el bloqueo: la espera no deja la hora vieja
        motivo = _motivo_no_elegible(usuario, ahora)

        # Siempre exactamente una verificación de hash, exista o no el usuario, esté o no bloqueado.
        encoded = dummy_hash() if motivo else usuario.password
        contrasena_correcta = check_password(contrasena, encoded)

        if motivo is None and not contrasena_correcta:
            motivo = MotivoFallo.CONTRASENA_INCORRECTA
            _registrar_fallo(usuario.pk, ahora)
        elif motivo is None:
            Usuario.objects.filter(pk=usuario.pk).update(intentos_fallidos=0, bloqueado_hasta=None)
            usuario.intentos_fallidos, usuario.bloqueado_hasta = 0, None

    if motivo is not None:
        logger.info("Inicio de sesión rechazado: motivo=%s", motivo.value)
        # HOOK evento: SESION_FALLIDA (también para correos inexistentes, sin guardar el correo).
        return ResultadoLogin(None, motivo)

    login(request, usuario, backend=SESSION_BACKEND)  # rota la clave de sesión (anti fijación)
    # HOOK evento: SESION_INICIADA.
    return ResultadoLogin(usuario, None)


def _registrar_fallo(usuario_pk: int, ahora) -> None:
    """Un único UPDATE atómico: dos fallos simultáneos nunca se pisan (no hay leer-modificar-
    guardar). Si el bloqueo anterior ya venció, este fallo abre un ciclo nuevo (cuenta 1)."""
    limite = settings.LOGIN_MAX_FAILED_ATTEMPTS
    hasta = ahora + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
    campo_fecha = DateTimeField()
    bloqueo_vencido = Q(bloqueado_hasta__lte=ahora)
    base = Case(When(bloqueo_vencido, then=Value(0)), default=F("intentos_fallidos"))
    nuevo = base + 1
    Usuario.objects.filter(pk=usuario_pk).update(
        intentos_fallidos=nuevo,
        bloqueado_hasta=Case(
            When(GreaterThanOrEqual(nuevo, Value(limite)), then=Value(hasta, campo_fecha)),
            When(bloqueo_vencido, then=Value(None, campo_fecha)),
            default=F("bloqueado_hasta"),
            output_field=campo_fecha,
        ),
    )


def cerrar_sesion(request) -> None:
    # HOOK evento: cierre de sesión (la definición no lista un tipo; ver decisiones.md).
    logout(request)  # borra la sesión en el servidor


def cambiar_contrasena(request, usuario_pk: int, actual: str, nueva: str) -> Usuario:
    usuario = Usuario.objects.get(pk=usuario_pk)
    if not usuario.check_password(actual):
        raise ContrasenaActualIncorrecta
    if actual == nueva:
        raise ContrasenaRepetida
    validate_password(nueva, usuario)  # ValidationError con los mensajes de los validadores

    with transaction.atomic():
        usuario.set_password(nueva)
        usuario.debe_cambiar_contrasena = False
        usuario.save(update_fields=["password", "debe_cambiar_contrasena"])
        # HOOK evento: USUARIO_CONTRASENA_CAMBIADA, en esta misma transacción.
    # Conserva la sesión actual; las demás dejan de ser válidas porque su hash de sesión
    # ya no coincide con la contraseña nueva.
    update_session_auth_hash(request, usuario)
    return usuario
