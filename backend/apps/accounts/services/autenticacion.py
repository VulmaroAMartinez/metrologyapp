"""Inicio y cierre de sesión. Las vistas solo validan forma y delegan aquí.

Un inicio de sesión fallido NO lanza una excepción: el contador y el evento SESION_FALLIDA se
confirman con la transacción y la vista responde 401. Los eventos se escriben en la misma
transacción que el cambio (apps.audit); nunca se registran contraseñas, hashes ni el correo de un
intento contra un usuario inexistente.

Puntos de inyección (para pruebas, nunca en producción): el reloj, el verificador de hash, la
lectura de la fila con candado y el registro de eventos. Se pasan como argumentos de
`iniciar_sesion` (`reloj`, `verificar_hash`) o se sustituyen con `sustituir(...)`.
"""

import functools
import logging
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.hashers import check_password, make_password
from django.db import OperationalError, transaction
from django.db.models import Case, DateTimeField, F, Q, Value, When
from django.db.models.lookups import GreaterThanOrEqual
from django.utils import timezone

from apps.audit.models import TipoEvento
from apps.audit.services import registrar_evento
from apps.core.network import client_ip

from .. import locks
from ..models import Usuario

logger = logging.getLogger(__name__)

SESSION_BACKEND = "django.contrib.auth.backends.ModelBackend"


class MotivoFallo(StrEnum):
    """Solo para uso interno (logs y eventos). Nunca se muestra al cliente."""

    NO_EXISTE = "no_existe"
    INACTIVO = "inactivo"
    BLOQUEADO = "bloqueado"
    SIN_CONTRASENA = "sin_contrasena"
    CONTRASENA_INCORRECTA = "contrasena_incorrecta"
    INTENTO_SIMULTANEO = "intento_simultaneo"  # otro intento contra la misma cuenta en curso


ETIQUETA_MOTIVO = {
    MotivoFallo.NO_EXISTE: "usuario no registrado",
    MotivoFallo.INACTIVO: "usuario inactivo",
    MotivoFallo.BLOQUEADO: "cuenta bloqueada",
    MotivoFallo.SIN_CONTRASENA: "usuario sin contraseña utilizable",
    MotivoFallo.CONTRASENA_INCORRECTA: "contraseña incorrecta",
    MotivoFallo.INTENTO_SIMULTANEO: "intento simultáneo contra la misma cuenta",
}


@dataclass(frozen=True)
class ResultadoLogin:
    usuario: Usuario | None
    motivo: MotivoFallo | None


@functools.cache
def dummy_hash() -> str:
    """Hash ficticio con los mismos parámetros que los reales, calculado una vez por proceso
    (AccountsConfig.ready lo precalcula) para que verificar contra él cueste lo mismo."""
    return make_password(secrets.token_urlsafe(32))


# --- puntos de inyección -------------------------------------------------------------------


def _obtener_y_bloquear(correo: str):
    """Lee la fila del usuario con FOR NO KEY UPDATE NOWAIT (o None si no existe).

    La fila queda bloqueada desde aquí hasta que se confirma la transacción, así ninguna ráfaga en
    paralelo pasa la comprobación de "no bloqueado" antes de que se registre un fallo. NOWAIT:
    quien no obtiene el candado NO espera (no se pueden saturar workers contra una cuenta). NO KEY
    UPDATE y no UPDATE: el evento del intento rechazado inserta una fila con llave foránea a este
    usuario (FOR KEY SHARE), y FOR UPDATE la haría esperar al otro intento."""
    return Usuario.objects.select_for_update(nowait=True, no_key=True).filter(correo=correo).first()


# Los valores por defecto se resuelven al llamar (no al importar): el reloj controlado de las
# pruebas parchea `django.utils.timezone.now`, y esto debe seguir viéndolo.
def _reloj_real():
    return timezone.now()


def _verificar_hash_real(contrasena, codificada):
    return check_password(contrasena, codificada)


def _registrar_evento_real(*args, **kwargs):
    return registrar_evento(*args, **kwargs)


@dataclass(frozen=True)
class PuntosDeLogin:
    """Valores por defecto = comportamiento real."""

    reloj: Callable = _reloj_real
    verificar_hash: Callable = _verificar_hash_real
    obtener_usuario_bloqueado: Callable = _obtener_y_bloquear
    registrar_evento: Callable = _registrar_evento_real


puntos = PuntosDeLogin()


@contextmanager
def sustituir(**cambios):
    """Sustituye puntos de inyección mientras dura el bloque (solo para pruebas)."""
    global puntos
    anterior = puntos
    puntos = replace(anterior, **cambios)
    try:
        yield
    finally:
        puntos = anterior


# --- inicio de sesión ---------------------------------------------------------------------------


def iniciar_sesion(
    request,
    correo: str,
    contrasena: str,
    *,
    reloj: Callable | None = None,
    verificar_hash: Callable | None = None,
) -> ResultadoLogin:
    """Intenta iniciar sesión. `request=None` solo sirve para probar fallos (el éxito necesita la
    sesión de la petición). `reloj` y `verificar_hash` son puntos de inyección explícitos.

    El contador, la sesión y el evento se confirman juntos, también en un fallo (401)."""
    correo = correo.strip().lower()
    ip = client_ip(request) if request is not None else None
    reloj = reloj or puntos.reloj
    verificar_hash = verificar_hash or puntos.verificar_hash
    usuario = None
    try:
        with transaction.atomic():
            usuario = puntos.obtener_usuario_bloqueado(correo)
            ahora = reloj()  # tras obtener el candado
            motivo, es_correcta = _verificar(usuario, contrasena, ahora, verificar_hash)
            motivo = _registrar_resultado(request, usuario, motivo, es_correcta, ahora, ip)
    except OperationalError as error:
        motivo = _rechazar_por_intento_simultaneo(error, correo, contrasena, ip, verificar_hash)

    if motivo is not None:
        logger.info("Inicio de sesión rechazado: motivo=%s", motivo.value)
        return ResultadoLogin(None, motivo)
    return ResultadoLogin(usuario, None)


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


def _verificar(usuario, contrasena, ahora, verificar_hash):
    """(motivo de rechazo o None, contraseña correcta). Siempre exactamente UNA verificación de
    hash, exista o no el usuario, para que el tiempo no delate si la cuenta existe."""
    motivo = _motivo_no_elegible(usuario, ahora)
    codificada = dummy_hash() if motivo else usuario.password
    return motivo, verificar_hash(contrasena, codificada)


def _registrar_resultado(request, usuario, motivo, es_correcta, ahora, ip):
    """Aplica el resultado dentro de la transacción; devuelve el motivo del fallo o None."""
    if motivo is None and es_correcta:
        _registrar_exito(request, usuario, ip)
        return None
    return _registrar_rechazo(usuario, motivo, ahora, ip)


def _registrar_exito(request, usuario, ip) -> None:
    Usuario.objects.filter(pk=usuario.pk).update(intentos_fallidos=0, bloqueado_hasta=None)
    usuario.intentos_fallidos, usuario.bloqueado_hasta = 0, None
    login(request, usuario, backend=SESSION_BACKEND)  # rota la clave (anti fijación)
    puntos.registrar_evento(TipoEvento.SESION_INICIADA, usuario, "Inicio de sesión correcto", ip=ip)


def _registrar_rechazo(usuario, motivo, ahora, ip) -> MotivoFallo:
    bloqueada_hasta = None
    if motivo is None:
        motivo = MotivoFallo.CONTRASENA_INCORRECTA
        bloqueada_hasta = _registrar_fallo(usuario.pk, ahora)
    _evento_sesion_fallida(usuario, motivo, ip)
    if bloqueada_hasta is not None:
        puntos.registrar_evento(
            TipoEvento.CUENTA_BLOQUEADA,
            usuario,
            "Cuenta bloqueada por intentos fallidos",
            despues={"bloqueado_hasta": bloqueada_hasta},
            ip=ip,
        )
    return motivo


def _rechazar_por_intento_simultaneo(error, correo, contrasena, ip, verificar_hash):
    """Solo "candado no disponible" (SQLSTATE 55P03, el NOWAIT de arriba) se convierte en un
    rechazo; cualquier otro error de base de datos (caída, deadlock, timeout, permisos...) se
    propaga. Quien no obtiene el candado se rechaza tras la misma verificación de hash que
    cualquier otro fallo."""
    if not locks.es_candado_no_disponible(error):
        raise
    verificar_hash(contrasena, dummy_hash())
    usuario = Usuario.objects.filter(correo=correo).first()  # lectura simple, sin candado
    with transaction.atomic():
        _evento_sesion_fallida(usuario, MotivoFallo.INTENTO_SIMULTANEO, ip)
    return MotivoFallo.INTENTO_SIMULTANEO


def _evento_sesion_fallida(usuario, motivo: MotivoFallo, ip) -> None:
    """Exactamente un SESION_FALLIDA por intento fallido, exista o no el usuario. Nunca guarda
    el correo escrito (ni en la descripción ni en el JSON): para un usuario inexistente el
    evento queda sin usuario."""
    puntos.registrar_evento(
        TipoEvento.SESION_FALLIDA,
        usuario,
        f"Inicio de sesión fallido: {ETIQUETA_MOTIVO[motivo]}",
        despues={"motivo": motivo.value},
        ip=ip,
    )


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
