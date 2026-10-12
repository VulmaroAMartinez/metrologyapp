import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.constantes import TAMANO_MAXIMO, TAMANO_POR_DEFECTO

from .models import Evento, TipoEvento
from .sanitize import sanear

SISTEMA = "sistema"


class EventoFueraDeTransaccionError(RuntimeError):
    """El cambio de negocio y su evento deben confirmarse o revertirse juntos."""


def registrar_evento(
    tipo,
    usuario,
    descripcion,
    antes=None,
    despues=None,
    instrumento_id=None,
    ip=None,
) -> Evento:
    """Inserta un evento. Debe llamarse dentro de `transaction.atomic()`, junto al cambio.

    `usuario=None` significa Sistema. `descripcion` va sin fecha. `antes`/`despues` solo
    aceptan los campos de la lista blanca (ver `sanitize`).
    """
    if not transaction.get_connection().in_atomic_block:
        raise EventoFueraDeTransaccionError(
            "registrar_evento debe llamarse dentro de transaction.atomic()."
        )
    if tipo not in TipoEvento.values:
        raise ValueError(f"Tipo de evento desconocido: {tipo!r}.")
    return Evento.objects.create(
        tipo=tipo,
        usuario=usuario,
        descripcion=descripcion,
        antes=sanear(antes, "antes"),
        despues=sanear(despues, "despues"),
        instrumento_id=instrumento_id,
        ip=ip,
    )


# --- lectura de la bitácora (solo ADMIN) ---


class CursorInvalidoError(ValueError):
    pass


@dataclass(frozen=True)
class FiltrosEventos:
    desde: date | None = None  # fechas locales de la instalación (settings.TIME_ZONE)
    hasta: date | None = None  # inclusive: abarca todo ese día local
    usuario: int | str | None = None  # id, o SISTEMA para los eventos sin usuario
    tipos: tuple[str, ...] = ()
    instrumento_id: int | None = None


def codificar_cursor(ocurrido_en: datetime, pk: int) -> str:
    carga = {"t": ocurrido_en.astimezone(UTC).isoformat(), "i": pk}
    return base64.urlsafe_b64encode(json.dumps(carga, separators=(",", ":")).encode()).decode()


def decodificar_cursor(texto: str) -> tuple[datetime, int]:
    """Posición (ocurrido_en, id) del último evento entregado. Un cursor alterado solo cambia
    la posición de lectura: no da acceso a nada más, así que no se firma."""
    try:
        carga = json.loads(base64.urlsafe_b64decode(texto.encode()))
        instante = datetime.fromisoformat(carga["t"])
        pk = carga["i"]
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeError, AttributeError) as error:
        raise CursorInvalidoError("El cursor no es válido.") from error
    if instante.tzinfo is None or isinstance(pk, bool) or not isinstance(pk, int) or pk < 1:
        raise CursorInvalidoError("El cursor no es válido.")
    return instante, pk


def limites_utc(desde: date | None, hasta: date | None):
    """(inicio inclusivo, fin exclusivo) en UTC a partir de fechas locales. Se calcula con la
    medianoche local de cada fecha, así un día con cambio de horario mide 23 o 25 horas."""
    zona = timezone.get_current_timezone()

    def medianoche(dia):
        return datetime.combine(dia, time.min, tzinfo=zona).astimezone(UTC)

    inicio = medianoche(desde) if desde else None
    fin = medianoche(hasta + timedelta(days=1)) if hasta else None
    return inicio, fin


def construir_consulta(filtros: FiltrosEventos, cursor: tuple[datetime, int] | None = None):
    """QuerySet ordenado por (ocurrido_en desc, id desc), listo para cortar con LIMIT.

    `only()` trae del usuario solo id y nombre: el hash de contraseña nunca sale de la base."""
    consulta = Evento.objects.select_related("usuario").only(
        "id",
        "ocurrido_en",
        "tipo",
        "descripcion",
        "antes",
        "despues",
        "ip",
        "instrumento_id",
        "usuario",
        "usuario__nombre",
    )
    inicio, fin = limites_utc(filtros.desde, filtros.hasta)
    if inicio:
        consulta = consulta.filter(ocurrido_en__gte=inicio)
    if fin:
        consulta = consulta.filter(ocurrido_en__lt=fin)
    if filtros.usuario == SISTEMA:
        consulta = consulta.filter(usuario__isnull=True)
    elif filtros.usuario is not None:
        consulta = consulta.filter(usuario_id=filtros.usuario)
    if filtros.tipos:
        consulta = consulta.filter(tipo__in=filtros.tipos)
    if filtros.instrumento_id is not None:
        consulta = consulta.filter(instrumento_id=filtros.instrumento_id)
    if cursor:
        instante, pk = cursor
        # Equivale a (ocurrido_en, id) < (instante, pk); el primer término acota el rango de índice.
        consulta = consulta.filter(
            Q(ocurrido_en__lte=instante) & (Q(ocurrido_en__lt=instante) | Q(id__lt=pk))
        )
    return consulta.order_by("-ocurrido_en", "-id")


def consultar_eventos(filtros: FiltrosEventos, cursor=None, tamano: int = TAMANO_POR_DEFECTO):
    """Una página de la bitácora y el cursor de la siguiente (None si era la última).

    Paginación por claves: ni COUNT(*) ni OFFSET. Pide una fila de más para saber si hay otra."""
    tamano = max(1, min(tamano, TAMANO_MAXIMO))
    filas = list(construir_consulta(filtros, cursor)[: tamano + 1])
    if len(filas) <= tamano:
        return filas, None
    filas = filas[:tamano]
    return filas, codificar_cursor(filas[-1].ocurrido_en, filas[-1].pk)
