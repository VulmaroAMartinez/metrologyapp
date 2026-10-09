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
from django.contrib.auth import SESSION_KEY, login, logout, update_session_auth_hash
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import (
    get_default_password_validators,
    validate_password,
)
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, OperationalError, transaction
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


# --- Gestión de usuarios (solo ADMIN, acción GESTIONAR_USUARIOS) ------------------------------
#
# Cada servicio corre en transaction.atomic() con su evento dentro. Antes de tocar nada se
# bloquean (FOR NO KEY UPDATE, en orden de pk) todas las filas de administradores activos: así
# dos administradores que operan a la vez se serializan, el actor se revalida con el estado más
# reciente y el sistema nunca queda sin un administrador activo. NO KEY UPDATE (y no UPDATE)
# para no bloquear las llaves foráneas de los eventos que referencian a estos usuarios.
# No hay borrado físico ni desbloqueo manual: ningún servicio toca intentos_fallidos ni
# bloqueado_hasta.

NOMBRE_MAX = 150
NOMINA_MAX = 20
CORREO_MAX = 254
ALFABETO_TEMPORAL = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
LONGITUD_TEMPORAL = 20  # mínimo 16; se sube si PASSWORD_MIN_LENGTH lo exige


class ErrorDeUsuario(Exception):
    """Error de negocio con código y estado HTTP; la vista lo traduce a {codigo, detalle}."""

    codigo = "error"
    estado = 400
    detalle = "Operación no permitida."

    def __init__(self, detalle=None):
        self.detalle = detalle or self.detalle
        super().__init__(self.detalle)


class DatosDeUsuarioInvalidos(ErrorDeUsuario):
    codigo = "datos_invalidos"


class CorreoDuplicado(ErrorDeUsuario):
    codigo = "correo_duplicado"
    detalle = "Ya existe un usuario con ese correo."


class NominaDuplicada(ErrorDeUsuario):
    codigo = "numero_nomina_duplicado"
    detalle = "Ya existe un usuario con ese número de nómina."


class AutoModificacionNoPermitida(ErrorDeUsuario):
    codigo = "auto_modificacion_no_permitida"
    estado = 403
    detalle = (
        "No puedes desactivarte, cambiar tu propio rol ni restablecer tu propia contraseña "
        "desde la administración (usa cambiar-contrasena)."
    )


class UltimoAdministrador(ErrorDeUsuario):
    codigo = "ultimo_administrador"
    estado = 409
    detalle = "El sistema debe conservar al menos un administrador activo."


class ActorNoVigente(ErrorDeUsuario):
    """El administrador que operaba dejó de serlo (o se desactivó) mientras se procesaba."""

    codigo = "permiso_denegado"
    estado = 403
    detalle = "No tienes permiso para realizar esta acción."


class UsuarioInexistente(ErrorDeUsuario):
    codigo = "no_encontrado"
    estado = 404
    detalle = "El usuario no existe."


def _limpiar_campos(campos: dict) -> dict:
    """Normaliza y valida solo los campos presentes: correo en minúsculas y sin espacios,
    nombre obligatorio, numero_nomina vacío -> None."""
    limpios = {}
    if "nombre" in campos:
        nombre = (campos["nombre"] or "").strip()
        if not nombre:
            raise DatosDeUsuarioInvalidos("El nombre es obligatorio.")
        if len(nombre) > NOMBRE_MAX:
            raise DatosDeUsuarioInvalidos(f"El nombre admite hasta {NOMBRE_MAX} caracteres.")
        limpios["nombre"] = nombre
    if "correo" in campos:
        correo = (campos["correo"] or "").strip().lower()
        try:
            validate_email(correo)
        except DjangoValidationError:
            raise DatosDeUsuarioInvalidos("El correo no es válido.") from None
        if len(correo) > CORREO_MAX:
            raise DatosDeUsuarioInvalidos("El correo es demasiado largo.")
        limpios["correo"] = correo
    if "numero_nomina" in campos:
        nomina = (campos["numero_nomina"] or "").strip() or None
        if nomina is not None and len(nomina) > NOMINA_MAX:
            raise DatosDeUsuarioInvalidos(
                f"El número de nómina admite hasta {NOMINA_MAX} caracteres."
            )
        limpios["numero_nomina"] = nomina
    return limpios


def _verificar_duplicados(correo, nomina, excluir_pk=None) -> None:
    otros = Usuario.objects.exclude(pk=excluir_pk) if excluir_pk else Usuario.objects.all()
    if correo is not None and otros.filter(correo__iexact=correo).exists():
        raise CorreoDuplicado
    if nomina is not None and otros.filter(numero_nomina__iexact=nomina).exists():
        raise NominaDuplicada


def _relanzar_unicidad(error: IntegrityError):
    """Dos envíos simultáneos del mismo formulario pasan la comprobación previa y uno choca
    con el índice único: se traduce al mismo error limpio (400), nunca un 500."""
    restriccion = getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) or ""
    if "nomina" in restriccion:
        raise NominaDuplicada from error
    if "correo" in restriccion:
        raise CorreoDuplicado from error
    raise error


def _bloquear_administradores(actor_pk, objetivo_pk=None):
    """Bloquea los administradores activos y, si no es uno de ellos, al objetivo.

    Devuelve (actor, objetivo, administradores_activos). Debe llamarse dentro de atomic()."""
    administradores = list(
        Usuario.objects.select_for_update(no_key=True)
        .filter(rol=Usuario.Rol.ADMIN, activo=True)
        .order_by("pk")
    )
    por_pk = {u.pk: u for u in administradores}
    actor = None
    if actor_pk is not None:
        actor = por_pk.get(actor_pk)
        if actor is None:
            raise ActorNoVigente
    objetivo = None
    if objetivo_pk is not None:
        objetivo = por_pk.get(objetivo_pk) or (
            Usuario.objects.select_for_update(no_key=True).filter(pk=objetivo_pk).first()
        )
        if objetivo is None:
            raise UsuarioInexistente
    return actor, objetivo, administradores


def _garantizar_administrador_restante(administradores, excluyendo_pk) -> None:
    if not any(a.pk != excluyendo_pk for a in administradores):
        raise UltimoAdministrador


def _no_a_si_mismo(actor, objetivo) -> None:
    if actor.pk == objetivo.pk:
        raise AutoModificacionNoPermitida


def _longitud_minima() -> int:
    minimas = [getattr(v, "min_length", 0) for v in get_default_password_validators()]
    return max([LONGITUD_TEMPORAL, *minimas])


def generar_contrasena_temporal(usuario=None) -> str:
    """Contraseña aleatoria del módulo `secrets` (nunca la escribe una persona): al menos 16
    caracteres (20 por defecto) con mayúsculas, minúsculas y dígitos, y que pasa los
    validadores. Se muestra una sola vez; nunca va a eventos, logs ni mensajes de error."""
    longitud = _longitud_minima()
    while True:
        candidata = "".join(secrets.choice(ALFABETO_TEMPORAL) for _ in range(longitud))
        if not (
            any(c.islower() for c in candidata)
            and any(c.isupper() for c in candidata)
            and any(c.isdigit() for c in candidata)
        ):
            continue
        try:
            validate_password(candidata, usuario)
        except DjangoValidationError:
            continue
        return candidata


def invalidar_sesiones(usuario_pk: int) -> int:
    """Borra de django_session las sesiones de un usuario.

    django_session no guarda el usuario en una columna, así que se decodifica cada sesión
    vigente; aceptable para el volumen de este sistema (decisiones.md)."""
    claves = [
        sesion.session_key
        for sesion in Session.objects.filter(expire_date__gt=timezone.now()).iterator()
        if str(sesion.get_decoded().get(SESSION_KEY)) == str(usuario_pk)
    ]
    if claves:
        Session.objects.filter(session_key__in=claves).delete()
    return len(claves)


def _datos(usuario) -> dict:
    return {
        "nombre": usuario.nombre,
        "correo": usuario.correo,
        "numero_nomina": usuario.numero_nomina,
        "rol": usuario.rol,
        "activo": usuario.activo,
    }


def _etiqueta(usuario) -> str:
    return f"{usuario.correo} (id {usuario.pk})"


def crear_usuario(actor_pk, *, nombre, correo, rol, numero_nomina=None, contrasena=None, ip=None):
    """Crea un usuario con cambio de contraseña obligatorio.

    Con `contrasena=None` el sistema genera una temporal (la API siempre así) y la devuelve:
    `(usuario, temporal)`. `actor_pk=None` es el Sistema (comando crear_admin, que sí recibe
    la contraseña por prompt). La temporal es la única vez que existe en claro."""
    if rol not in Usuario.Rol.values:
        raise DatosDeUsuarioInvalidos("El rol no es válido.")
    campos = _limpiar_campos({"nombre": nombre, "correo": correo, "numero_nomina": numero_nomina})
    temporal = None
    try:
        with transaction.atomic():
            actor, _, _ = _bloquear_administradores(actor_pk)
            _verificar_duplicados(campos["correo"], campos["numero_nomina"])
            candidato = Usuario(nombre=campos["nombre"], correo=campos["correo"])
            if contrasena is None:
                temporal = contrasena = generar_contrasena_temporal(candidato)
            else:
                try:
                    validate_password(contrasena, candidato)
                except DjangoValidationError as error:
                    raise DatosDeUsuarioInvalidos(" ".join(error.messages)) from None
            usuario = Usuario.objects.create_user(
                campos["correo"],
                campos["nombre"],
                rol,
                password=contrasena,
                numero_nomina=campos["numero_nomina"],
                debe_cambiar_contrasena=True,
            )
            registrar_evento(
                TipoEvento.USUARIO_CREADO,
                actor,
                f"Usuario creado: {_etiqueta(usuario)}, rol {usuario.rol}",
                despues={**_datos(usuario), "debe_cambiar_contrasena": True},
                ip=ip,
            )
    except IntegrityError as error:
        _relanzar_unicidad(error)
    return usuario, temporal


def editar_usuario(actor_pk, objetivo_pk, campos: dict, ip=None) -> Usuario:
    """Edita nombre, correo y/o numero_nomina. Sin cambios reales no hay escritura ni evento."""
    limpios = _limpiar_campos(campos)
    try:
        with transaction.atomic():
            actor, objetivo, _ = _bloquear_administradores(actor_pk, objetivo_pk)
            cambios = {k: v for k, v in limpios.items() if getattr(objetivo, k) != v}
            if not cambios:
                return objetivo
            _verificar_duplicados(
                cambios.get("correo"), cambios.get("numero_nomina"), excluir_pk=objetivo.pk
            )
            antes = {k: getattr(objetivo, k) for k in cambios}
            for campo, valor in cambios.items():
                setattr(objetivo, campo, valor)
            objetivo.save(update_fields=list(cambios))
            registrar_evento(
                TipoEvento.USUARIO_EDITADO,
                actor,
                f"Usuario editado: {_etiqueta(objetivo)} ({', '.join(cambios)})",
                antes=antes,
                despues=cambios,
                ip=ip,
            )
    except IntegrityError as error:
        _relanzar_unicidad(error)
    return objetivo


def cambiar_rol(actor_pk, objetivo_pk, nuevo_rol: str, ip=None) -> Usuario:
    if nuevo_rol not in Usuario.Rol.values:
        raise DatosDeUsuarioInvalidos("El rol no es válido.")
    with transaction.atomic():
        actor, objetivo, administradores = _bloquear_administradores(actor_pk, objetivo_pk)
        _no_a_si_mismo(actor, objetivo)
        if objetivo.rol == nuevo_rol:
            return objetivo
        if objetivo.rol == Usuario.Rol.ADMIN and objetivo.activo:
            _garantizar_administrador_restante(administradores, objetivo.pk)
        antes = objetivo.rol
        objetivo.rol = nuevo_rol
        objetivo.save(update_fields=["rol"])
        # Las sesiones abiertas siguen valiendo, pero el rol se lee de la base en cada
        # petición: los permisos nuevos rigen desde la siguiente (decisiones.md).
        registrar_evento(
            TipoEvento.USUARIO_ROL_CAMBIADO,
            actor,
            f"Rol de {_etiqueta(objetivo)} cambiado de {antes} a {nuevo_rol}",
            antes={"rol": antes},
            despues={"rol": nuevo_rol},
            ip=ip,
        )
    return objetivo


def desactivar_usuario(actor_pk, objetivo_pk, ip=None) -> Usuario:
    with transaction.atomic():
        actor, objetivo, administradores = _bloquear_administradores(actor_pk, objetivo_pk)
        _no_a_si_mismo(actor, objetivo)
        if not objetivo.activo:
            return objetivo
        if objetivo.rol == Usuario.Rol.ADMIN:
            _garantizar_administrador_restante(administradores, objetivo.pk)
        objetivo.activo = False
        objetivo.save(update_fields=["activo"])
        invalidar_sesiones(objetivo.pk)
        registrar_evento(
            TipoEvento.USUARIO_DESACTIVADO,
            actor,
            f"Usuario desactivado: {_etiqueta(objetivo)}",
            antes={"activo": True},
            despues={"activo": False},
            ip=ip,
        )
    return objetivo


def reactivar_usuario(actor_pk, objetivo_pk, ip=None) -> Usuario:
    """Reactiva la cuenta. No es un desbloqueo: no toca intentos_fallidos ni bloqueado_hasta."""
    with transaction.atomic():
        actor, objetivo, _ = _bloquear_administradores(actor_pk, objetivo_pk)
        if objetivo.activo:
            return objetivo
        objetivo.activo = True
        objetivo.save(update_fields=["activo"])
        registrar_evento(
            TipoEvento.USUARIO_REACTIVADO,
            actor,
            f"Usuario reactivado: {_etiqueta(objetivo)}",
            antes={"activo": False},
            despues={"activo": True},
            ip=ip,
        )
    return objetivo


def restablecer_contrasena(actor_pk, objetivo_pk, ip=None):
    """Genera una contraseña temporal y exige cambiarla. Devuelve (usuario, temporal).

    Un administrador no puede restablecerse a sí mismo por aquí: su vía es cambiar-contrasena,
    que pide la contraseña actual. NO modifica intentos_fallidos ni bloqueado_hasta: sería un
    desbloqueo manual, descartado por decisión del equipo."""
    with transaction.atomic():
        actor, objetivo, _ = _bloquear_administradores(actor_pk, objetivo_pk)
        _no_a_si_mismo(actor, objetivo)
        temporal = generar_contrasena_temporal(objetivo)
        objetivo.set_password(temporal)
        objetivo.debe_cambiar_contrasena = True
        objetivo.save(update_fields=["password", "debe_cambiar_contrasena"])
        invalidar_sesiones(objetivo.pk)
        registrar_evento(
            TipoEvento.USUARIO_CONTRASENA_RESTABLECIDA,
            actor,
            f"Contraseña restablecida para {_etiqueta(objetivo)}",
            despues={"debe_cambiar_contrasena": True},
            ip=ip,
        )
    return objetivo, temporal
