"""Reglas de negocio de la autenticación. Las vistas solo validan forma y delegan aquí.

Los eventos se escriben en la misma transacción que el cambio (apps.audit). Nunca se
registran contraseñas, hashes ni el correo de un intento contra un usuario inexistente.
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
from django.db import OperationalError, transaction
from django.db.models import Case, DateTimeField, F, Q, Value, When
from django.db.models.lookups import GreaterThanOrEqual
from django.utils import timezone

from apps.audit.models import TipoEvento
from apps.audit.services import registrar_evento
from apps.core.network import client_ip

from .models import Usuario

logger = logging.getLogger(__name__)

SESSION_BACKEND = "django.contrib.auth.backends.ModelBackend"
SQLSTATE_LOCK_NOT_AVAILABLE = "55P03"  # PostgreSQL: lock_not_available


class MotivoFallo(StrEnum):
    """Solo para uso interno (logs y eventos). Nunca se muestra al cliente."""

    NO_EXISTE = "no_existe"
    INACTIVO = "inactivo"
    BLOQUEADO = "bloqueado"
    SIN_CONTRASENA = "sin_contrasena"
    CONTRASENA_INCORRECTA = "contrasena_incorrecta"
    INTENTO_SIMULTANEO = "intento_simultaneo"  # otro intento contra la misma cuenta en curso


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


ETIQUETA_MOTIVO = {
    MotivoFallo.NO_EXISTE: "usuario no registrado",
    MotivoFallo.INACTIVO: "usuario inactivo",
    MotivoFallo.BLOQUEADO: "cuenta bloqueada",
    MotivoFallo.SIN_CONTRASENA: "usuario sin contraseña utilizable",
    MotivoFallo.CONTRASENA_INCORRECTA: "contraseña incorrecta",
    MotivoFallo.INTENTO_SIMULTANEO: "intento simultáneo contra la misma cuenta",
}


def _evento_sesion_fallida(usuario, motivo: MotivoFallo, ip) -> None:
    """Exactamente un SESION_FALLIDA por intento fallido, exista o no el usuario. Nunca guarda
    el correo escrito (ni en la descripción ni en el JSON): para un usuario inexistente el
    evento queda sin usuario."""
    registrar_evento(
        TipoEvento.SESION_FALLIDA,
        usuario,
        f"Inicio de sesión fallido: {ETIQUETA_MOTIVO[motivo]}",
        despues={"motivo": motivo.value},
        ip=ip,
    )


def iniciar_sesion(request, correo: str, contrasena: str) -> ResultadoLogin:
    # La fila queda bloqueada (FOR NO KEY UPDATE NOWAIT) desde que se lee hasta que se registra el
    # resultado, así ninguna ráfaga en paralelo pasa la comprobación de "no bloqueado" antes de
    # que se registre un fallo. NOWAIT: quien no obtiene el candado NO espera (no se pueden
    # saturar workers contra una cuenta) y se rechaza tras la misma verificación de hash que
    # cualquier otro fallo, para que el tiempo no delate que la cuenta existe.
    # NO KEY UPDATE y no UPDATE: el evento del intento rechazado inserta una fila con llave
    # foránea a este usuario (FOR KEY SHARE), y FOR UPDATE la haría esperar al otro intento.
    # El contador, la sesión y el evento se confirman juntos, también en un fallo (401).
    correo = correo.strip().lower()
    ip = client_ip(request) if request is not None else None
    usuario = None
    try:
        with transaction.atomic():
            usuario = (
                Usuario.objects.select_for_update(nowait=True, no_key=True)
                .filter(correo=correo)
                .first()
            )
            ahora = timezone.now()  # tras obtener el candado
            motivo = _motivo_no_elegible(usuario, ahora)

            # Siempre exactamente una verificación de hash, exista o no el usuario.
            encoded = dummy_hash() if motivo else usuario.password
            contrasena_correcta = check_password(contrasena, encoded)

            if motivo is None and contrasena_correcta:
                Usuario.objects.filter(pk=usuario.pk).update(
                    intentos_fallidos=0, bloqueado_hasta=None
                )
                usuario.intentos_fallidos, usuario.bloqueado_hasta = 0, None
                login(request, usuario, backend=SESSION_BACKEND)  # rota la clave (anti fijación)
                registrar_evento(
                    TipoEvento.SESION_INICIADA, usuario, "Inicio de sesión correcto", ip=ip
                )
            else:
                bloqueada_hasta = None
                if motivo is None:
                    motivo = MotivoFallo.CONTRASENA_INCORRECTA
                    bloqueada_hasta = _registrar_fallo(usuario.pk, ahora)
                _evento_sesion_fallida(usuario, motivo, ip)
                if bloqueada_hasta is not None:
                    registrar_evento(
                        TipoEvento.CUENTA_BLOQUEADA,
                        usuario,
                        "Cuenta bloqueada por intentos fallidos",
                        despues={"bloqueado_hasta": bloqueada_hasta},
                        ip=ip,
                    )
    except OperationalError as error:
        # Solo "candado no disponible" (SQLSTATE 55P03, el NOWAIT de arriba). Cualquier otro
        # error de base de datos (caída, deadlock, timeout, permisos...) se propaga.
        if getattr(error.__cause__, "sqlstate", None) != SQLSTATE_LOCK_NOT_AVAILABLE:
            raise
        check_password(contrasena, dummy_hash())
        motivo = MotivoFallo.INTENTO_SIMULTANEO
        usuario = Usuario.objects.filter(correo=correo).first()  # lectura simple, sin candado
        with transaction.atomic():
            _evento_sesion_fallida(usuario, motivo, ip)

    if motivo is not None:
        logger.info("Inicio de sesión rechazado: motivo=%s", motivo.value)
        return ResultadoLogin(None, motivo)
    return ResultadoLogin(usuario, None)


def _registrar_fallo(usuario_pk: int, ahora):
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
    # Devuelve hasta cuándo quedó bloqueada si ESTE fallo la bloqueó; si no, None.
    bloqueado_hasta = (
        Usuario.objects.filter(pk=usuario_pk).values_list("bloqueado_hasta", flat=True).first()
    )
    return bloqueado_hasta if bloqueado_hasta is not None and bloqueado_hasta > ahora else None


def cerrar_sesion(request) -> None:
    # Sin evento: DEFINICION §6.9 no define un tipo para el cierre de sesión (ver decisiones.md).
    logout(request)  # borra la sesión en el servidor


def cambiar_contrasena(request, usuario_pk: int, actual: str, nueva: str) -> Usuario:
    usuario = Usuario.objects.get(pk=usuario_pk)
    if not usuario.check_password(actual):
        raise ContrasenaActualIncorrecta
    if actual == nueva:
        raise ContrasenaRepetida
    validate_password(nueva, usuario)  # ValidationError con los mensajes de los validadores

    ip = client_ip(request)
    with transaction.atomic():
        antes = {"debe_cambiar_contrasena": usuario.debe_cambiar_contrasena}
        usuario.set_password(nueva)
        usuario.debe_cambiar_contrasena = False
        usuario.save(update_fields=["password", "debe_cambiar_contrasena"])
        registrar_evento(
            TipoEvento.USUARIO_CONTRASENA_CAMBIADA,
            usuario,
            "El usuario cambió su contraseña",
            antes=antes,
            despues={"debe_cambiar_contrasena": False},
            ip=ip,
        )
        # Conserva la sesión actual; las demás dejan de ser válidas porque su hash de sesión
        # ya no coincide con la contraseña nueva.
        update_session_auth_hash(request, usuario)
    return usuario
