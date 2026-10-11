"""Gestión de usuarios (solo ADMIN, acción GESTIONAR_USUARIOS), cambio de la contraseña propia y
los errores de negocio de la cuenta.

Cada operación hace FUERA de la transacción todo lo caro (campos, validadores, hash Argon2) y
DENTRO de `seccion_critica()` solo lo que necesita candados, volviendo a comprobar con el estado
fresco: actor vigente, objetivo, duplicados. Orden de candados y regla de escritura de
`rol`/`activo`: `apps/accounts/locks.py`. El cierre de sesiones se programa con on_commit; la
invalidación real es `sesion_version`. No hay borrado físico ni desbloqueo manual: ningún servicio
toca intentos_fallidos ni bloqueado_hasta.

Puntos de inyección (para pruebas, nunca en producción): `puntos` agrupa lo que las pruebas
necesitan sustituir (registro de eventos, cifrado, cierre de sesiones, comprobación de duplicados,
alta en la base de datos, candado de administradores) y `sustituir(...)` los cambia en un bloque.
"""

import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace

from django.contrib.auth import SESSION_KEY, update_session_auth_hash
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.audit.models import TipoEvento
from apps.audit.services import registrar_evento
from apps.core.network import client_ip

from .. import locks
from ..models import Usuario
from ..permissions import Accion, puede

logger = logging.getLogger(__name__)

NOMBRE_MAX = 150
NOMINA_MAX = 20
CORREO_MAX = 254


# --- errores de negocio ---------------------------------------------------------------------------


class ErrorDeUsuario(APIException):
    """Error de negocio de la administración de usuarios y del cambio de contraseña.

    Es una APIException: las vistas no lo capturan, el manejador de excepciones lo responde con
    el sobre {codigo, detalle[, campos]} y el estado HTTP. `campos` ({campo: [mensajes]}) solo
    cuando el error es de campo. Los servicios también se usan desde comandos de gestión, donde
    es una excepción corriente (`detalle` es el texto). NO se usa en `iniciar_sesion`: un
    inicio de sesión fallido no lanza (el contador y el evento se confirman con un 401)."""

    codigo = "error"
    estado = 400
    detalle = "Operación no permitida."

    def __init__(self, detalle=None, *, campos=None):
        self.detalle = detalle or type(self).detalle
        self.campos = campos
        cuerpo = {"codigo": self.codigo, "detalle": self.detalle}
        if campos:
            cuerpo["campos"] = campos
        super().__init__(detail=cuerpo)

    @property
    def status_code(self):
        return self.estado

    def __str__(self):
        return str(self.detalle)


class DatosDeUsuarioInvalidos(ErrorDeUsuario):
    codigo = "datos_invalidos"


def _invalido(campo, mensaje):
    return DatosDeUsuarioInvalidos(mensaje, campos={campo: [mensaje]})


class ContrasenaActualIncorrecta(DatosDeUsuarioInvalidos):
    def __init__(self):
        mensaje = "La contraseña actual no es correcta."
        super().__init__(mensaje, campos={"contrasena_actual": [mensaje]})


class ContrasenaRepetida(DatosDeUsuarioInvalidos):
    def __init__(self):
        mensaje = "La contraseña nueva debe ser distinta."
        super().__init__(mensaje, campos={"contrasena_nueva": [mensaje]})


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


class ReintentarMasTarde(ErrorDeUsuario):
    """No se obtuvo un candado a tiempo (lock_timeout); la operación se revirtió entera."""

    codigo = "reintentar"
    estado = 503
    detalle = (
        "El sistema está ocupado con otra operación de administración. Reintenta en unos segundos."
    )


class ContrasenaModificada(ErrorDeUsuario):
    """La contraseña cambió entre verificarla y aplicar el cambio (p. ej. la restableció otro
    administrador)."""

    codigo = "contrasena_modificada"
    estado = 409
    detalle = "La contraseña cambió mientras se procesaba tu solicitud. Vuelve a intentarlo."


# --- validación y normalización ------------------------------------------------------------------


def limpiar_campos(campos: dict) -> dict:
    """Normaliza y valida solo los campos presentes: correo en minúsculas y sin espacios,
    nombre obligatorio, numero_nomina vacío -> None. Correo y numero_nomina solo admiten ASCII
    (decisión de Vul): `.lower()` de Python y `LOWER()` de PostgreSQL difieren fuera de ASCII."""
    limpios = {}
    if "nombre" in campos:
        nombre = (campos["nombre"] or "").strip()
        if not nombre:
            raise _invalido("nombre", "El nombre es obligatorio.")
        if len(nombre) > NOMBRE_MAX:
            raise _invalido("nombre", f"El nombre admite hasta {NOMBRE_MAX} caracteres.")
        limpios["nombre"] = nombre
    if "correo" in campos:
        correo = (campos["correo"] or "").strip().lower()
        if not correo.isascii():
            raise _invalido("correo", "El correo solo admite caracteres ASCII.")
        try:
            validate_email(correo)
        except DjangoValidationError:
            raise _invalido("correo", "El correo no es válido.") from None
        if len(correo) > CORREO_MAX:
            raise _invalido("correo", "El correo es demasiado largo.")
        limpios["correo"] = correo
    if "numero_nomina" in campos:
        nomina = (campos["numero_nomina"] or "").strip() or None
        if nomina is not None and not nomina.isascii():
            raise _invalido("numero_nomina", "El número de nómina solo admite caracteres ASCII.")
        if nomina is not None and len(nomina) > NOMINA_MAX:
            raise _invalido(
                "numero_nomina", f"El número de nómina admite hasta {NOMINA_MAX} caracteres."
            )
        limpios["numero_nomina"] = nomina
    return limpios


def validar_contrasena(contrasena: str, usuario, campo: str = "contrasena") -> None:
    """Pasa la contraseña por los validadores. Los mensajes describen la regla incumplida y
    nunca incluyen la contraseña; ella no se devuelve, ni se registra, ni va a un evento."""
    try:
        validate_password(contrasena, usuario)
    except DjangoValidationError as error:
        mensajes = list(error.messages)
        raise DatosDeUsuarioInvalidos(" ".join(mensajes), campos={campo: mensajes}) from None


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


# --- sesiones: limpieza posterior al commit -------------------------------------------------------


def invalidar_sesiones(usuario_pk: int) -> int:
    """Borra de django_session las sesiones de un usuario.

    django_session no guarda el usuario en una columna, así que se decodifica cada sesión
    vigente; aceptable para el volumen de este sistema (decisiones.md: ~1 ms con 300 sesiones).
    Es SOLO limpieza posterior al commit: la invalidación real es `sesion_version` (entra en el
    hash de sesión), que no depende de que este borrado ocurra."""
    claves = [
        sesion.session_key
        for sesion in Session.objects.filter(expire_date__gt=timezone.now()).iterator()
        if str(sesion.get_decoded().get(SESSION_KEY)) == str(usuario_pk)
    ]
    if claves:
        Session.objects.filter(session_key__in=claves).delete()
    return len(claves)


def _invalidar_sesiones_anteriores(objetivo) -> None:
    """Sube `sesion_version` con un UPDATE atómico (F), en la misma transacción y bajo los mismos
    candados que la operación: toda sesión creada antes deja de coincidir con el hash de sesión,
    para siempre y sin depender de ningún borrado. El borrado de filas es solo limpieza."""
    Usuario.objects.filter(pk=objetivo.pk).update(sesion_version=F("sesion_version") + 1)
    objetivo.refresh_from_db(fields=["sesion_version"])


def _cerrar_sesiones(usuario_pk: int) -> None:
    try:
        puntos.cerrar_sesiones(usuario_pk)
    except Exception as error:  # noqa: BLE001 - tras el commit nada debe tumbar la respuesta
        # Solo el id y el tipo de error: sin correo, sesión ni datos del usuario.
        logger.error(
            "No se pudieron borrar las sesiones de usuario_id=%s (%s); siguen inválidas "
            "por la versión de sesión",
            usuario_pk,
            type(error).__name__,
        )


def _programar_cierre_de_sesiones(usuario_pk: int) -> None:
    """Se ejecuta al confirmar la transacción, ya sin candados."""
    transaction.on_commit(lambda: _cerrar_sesiones(usuario_pk), robust=True)


# --- sección crítica y candados -----------------------------------------------------------------


@contextmanager
def seccion_critica():
    """Transacción de una operación de administración.

    Si un candado no se obtiene a tiempo (lock_timeout), la transacción se revierte entera y se
    responde "reintenta": nunca queda un cambio a medias."""
    try:
        with transaction.atomic():
            yield
    except OperationalError as error:
        if not locks.es_candado_no_disponible(error):
            raise
        raise ReintentarMasTarde from error


def _bloquear(actor_pk, objetivo_pk=None):
    """L0 y, si hay objetivo, L1 de SU fila (ver `locks`). Debe llamarse dentro de
    `seccion_critica()`. Los administradores se leen sin candado de fila: L0 basta porque toda
    escritura de `rol` o `activo` la toma.

    Devuelve (actor, objetivo, administradores_activos); el actor se revalida con el estado más
    reciente y el objetivo llega fresco, ya bloqueado."""
    locks.fijar_lock_timeout()
    puntos.tomar_candado_de_administradores()
    administradores = list(
        Usuario.objects.filter(rol=Usuario.Rol.ADMIN, activo=True).order_by("pk")
    )
    actor = None
    if actor_pk is not None:
        actor = next((u for u in administradores if u.pk == actor_pk), None)
        if actor is None:
            raise ActorNoVigente
    objetivo = None
    if objetivo_pk is not None:
        objetivo = Usuario.objects.select_for_update(no_key=True).filter(pk=objetivo_pk).first()
        if objetivo is None:
            raise UsuarioInexistente
    return actor, objetivo, administradores


def _garantizar_administrador_restante(administradores, excluyendo_pk) -> None:
    if not any(a.pk != excluyendo_pk for a in administradores):
        raise UltimoAdministrador


def _no_a_si_mismo(actor, objetivo) -> None:
    if actor.pk == objetivo.pk:
        raise AutoModificacionNoPermitida


# --- puntos de inyección --------------------------------------------------------------------------


def _crear_en_bd(correo, nombre, rol, *, contrasena_cifrada, numero_nomina):
    """Único llamador del manager de bajo nivel (lo comprueba test_admin_lock_rule)."""
    return Usuario.objects.create_user(
        correo,
        nombre,
        rol,
        contrasena_cifrada=contrasena_cifrada,
        numero_nomina=numero_nomina,
    )


@dataclass(frozen=True)
class Puntos:
    """Valores por defecto = comportamiento real. Las pruebas los sustituyen con `sustituir`."""

    registrar_evento: Callable = registrar_evento
    cifrar_contrasena: Callable = make_password
    cerrar_sesiones: Callable = invalidar_sesiones
    verificar_duplicados: Callable = _verificar_duplicados
    crear_usuario_en_bd: Callable = _crear_en_bd
    tomar_candado_de_administradores: Callable = locks.tomar_candado_de_administradores


puntos = Puntos()


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


# --- operaciones ---------------------------------------------------------------------------


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


def cambiar_contrasena(request, usuario_pk: int, actual: str, nueva: str) -> Usuario:
    """Cambio de la contraseña propia (solo ADMIN: acción CAMBIAR_PROPIA_CONTRASENA).

    La verificación y el hash (Argon2) ocurren ANTES de la transacción. Dentro, con la fila
    bloqueada, se comprueba que la contraseña guardada sigue siendo EXACTAMENTE aquella contra la
    que se verificó (la "versión"): si otro administrador la restableció entretanto, 409 en vez
    de pisar su cambio."""
    usuario = Usuario.objects.get(pk=usuario_pk)
    leida = usuario.password
    if not check_password(actual, leida):
        raise ContrasenaActualIncorrecta
    if actual == nueva:
        raise ContrasenaRepetida
    validar_contrasena(nueva, usuario, "contrasena_nueva")
    nueva_cifrada = puntos.cifrar_contrasena(nueva)

    ip = client_ip(request)
    with seccion_critica():
        locks.fijar_lock_timeout()
        fresco = Usuario.objects.select_for_update(no_key=True).get(pk=usuario_pk)
        if fresco.password != leida:
            raise ContrasenaModificada
        if not fresco.activo or not puede(fresco.rol, Accion.CAMBIAR_PROPIA_CONTRASENA):
            raise ActorNoVigente
        fresco.password = nueva_cifrada
        fresco.save(update_fields=["password"])
        # Sin antes/después: la contraseña nunca va a la bitácora, ni siquiera su hash.
        puntos.registrar_evento(
            TipoEvento.USUARIO_CONTRASENA_CAMBIADA,
            fresco,
            "El administrador cambió su propia contraseña",
            ip=ip,
        )
        # Conserva la sesión actual; las demás dejan de ser válidas porque su hash de sesión
        # ya no coincide con la contraseña nueva.
        update_session_auth_hash(request, fresco)
    return fresco


def crear_usuario(actor_pk, *, nombre, correo, rol, contrasena, numero_nomina=None, ip=None):
    """Crea un usuario. Solo el administrador asigna contraseñas: la escribe él (o quien crea
    el primer administrador con `crear_admin`) y pasa por los validadores. `actor_pk=None` es
    el Sistema. La contraseña no se devuelve ni se registra en ningún evento.

    El hash (Argon2) se calcula ANTES de abrir la sección crítica; dentro se revalida al actor y
    los duplicados."""
    if rol not in Usuario.Rol.values:
        raise _invalido("rol", "El rol no es válido.")
    campos = limpiar_campos({"nombre": nombre, "correo": correo, "numero_nomina": numero_nomina})
    puntos.verificar_duplicados(campos["correo"], campos["numero_nomina"])  # rápido, sin candados
    validar_contrasena(contrasena, Usuario(nombre=campos["nombre"], correo=campos["correo"]))
    cifrada = puntos.cifrar_contrasena(contrasena)
    try:
        with seccion_critica():
            actor, _, _ = _bloquear(actor_pk)
            puntos.verificar_duplicados(campos["correo"], campos["numero_nomina"])
            usuario = puntos.crear_usuario_en_bd(
                campos["correo"],
                campos["nombre"],
                rol,
                contrasena_cifrada=cifrada,
                numero_nomina=campos["numero_nomina"],
            )
            puntos.registrar_evento(
                TipoEvento.USUARIO_CREADO,
                actor,
                f"Usuario creado: {_etiqueta(usuario)}, rol {usuario.rol}",
                despues=_datos(usuario),
                ip=ip,
            )
    except IntegrityError as error:
        _relanzar_unicidad(error)
    return usuario


def editar_usuario(actor_pk, objetivo_pk, campos: dict, ip=None) -> Usuario:
    """Edita nombre, correo y/o numero_nomina. Sin cambios reales no hay escritura ni evento."""
    limpios = limpiar_campos(campos)
    try:
        with seccion_critica():
            actor, objetivo, _ = _bloquear(actor_pk, objetivo_pk)
            cambios = {k: v for k, v in limpios.items() if getattr(objetivo, k) != v}
            if not cambios:
                return objetivo
            puntos.verificar_duplicados(
                cambios.get("correo"), cambios.get("numero_nomina"), excluir_pk=objetivo.pk
            )
            antes = {k: getattr(objetivo, k) for k in cambios}
            for campo, valor in cambios.items():
                setattr(objetivo, campo, valor)
            objetivo.save(update_fields=list(cambios))
            puntos.registrar_evento(
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
        raise _invalido("rol", "El rol no es válido.")
    with seccion_critica():
        actor, objetivo, administradores = _bloquear(actor_pk, objetivo_pk)
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
        puntos.registrar_evento(
            TipoEvento.USUARIO_ROL_CAMBIADO,
            actor,
            f"Rol de {_etiqueta(objetivo)} cambiado de {antes} a {nuevo_rol}",
            antes={"rol": antes},
            despues={"rol": nuevo_rol},
            ip=ip,
        )
    return objetivo


def desactivar_usuario(actor_pk, objetivo_pk, ip=None) -> Usuario:
    with seccion_critica():
        actor, objetivo, administradores = _bloquear(actor_pk, objetivo_pk)
        _no_a_si_mismo(actor, objetivo)
        if not objetivo.activo:
            return objetivo
        if objetivo.rol == Usuario.Rol.ADMIN:
            _garantizar_administrador_restante(administradores, objetivo.pk)
        objetivo.activo = False
        objetivo.save(update_fields=["activo"])
        _invalidar_sesiones_anteriores(objetivo)
        _programar_cierre_de_sesiones(objetivo.pk)
        puntos.registrar_evento(
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
    with seccion_critica():
        actor, objetivo, _ = _bloquear(actor_pk, objetivo_pk)
        if objetivo.activo:
            return objetivo
        objetivo.activo = True
        objetivo.save(update_fields=["activo"])
        # Solo limpieza: las sesiones anteriores ya son inválidas por `sesion_version`.
        _programar_cierre_de_sesiones(objetivo.pk)
        puntos.registrar_evento(
            TipoEvento.USUARIO_REACTIVADO,
            actor,
            f"Usuario reactivado: {_etiqueta(objetivo)}",
            antes={"activo": False},
            despues={"activo": True},
            ip=ip,
        )
    return objetivo


def restablecer_contrasena(actor_pk, objetivo_pk, contrasena, ip=None, solo_rol=None):
    """El administrador fija una contraseña nueva a OTRO usuario (incluido el otro
    administrador), sin reautenticación. `actor_pk=None` es el Sistema (comando
    `restablecer_admin`, con `solo_rol=ADMIN`).

    Un administrador no puede restablecerse a sí mismo por aquí: su vía es cambiar-contrasena,
    que pide la contraseña actual. NO modifica intentos_fallidos ni bloqueado_hasta: sería un
    desbloqueo manual, descartado por decisión del equipo. Invalida las sesiones del afectado
    (`sesion_version`) y borra sus filas al confirmar la transacción.

    Validación y hash (Argon2) ocurren ANTES de la sección crítica, con una lectura previa del
    objetivo. Dentro se revalida todo con el objetivo ya bloqueado: si su nombre o correo
    cambiaron, se vuelven a pasar los validadores (el hash no depende de ellos)."""
    previo = Usuario.objects.filter(pk=objetivo_pk).first()
    if previo is None:
        raise UsuarioInexistente
    if actor_pk is not None and actor_pk == previo.pk:
        raise AutoModificacionNoPermitida
    if solo_rol is not None and previo.rol != solo_rol:
        raise DatosDeUsuarioInvalidos("El usuario no es administrador.")
    validar_contrasena(contrasena, previo)
    cifrada = puntos.cifrar_contrasena(contrasena)
    with seccion_critica():
        actor, objetivo, _ = _bloquear(actor_pk, objetivo_pk)
        if actor is not None:
            _no_a_si_mismo(actor, objetivo)
        if solo_rol is not None and objetivo.rol != solo_rol:
            raise DatosDeUsuarioInvalidos("El usuario no es administrador.")
        if (objetivo.nombre, objetivo.correo) != (previo.nombre, previo.correo):
            validar_contrasena(contrasena, objetivo)
        objetivo.password = cifrada
        objetivo.save(update_fields=["password"])
        _invalidar_sesiones_anteriores(objetivo)
        _programar_cierre_de_sesiones(objetivo.pk)
        origen = " por comando de servidor" if actor is None else ""
        puntos.registrar_evento(
            TipoEvento.USUARIO_CONTRASENA_RESTABLECIDA,
            actor,
            f"Contraseña restablecida para {_etiqueta(objetivo)}{origen}",
            ip=ip,
        )
    return objetivo
