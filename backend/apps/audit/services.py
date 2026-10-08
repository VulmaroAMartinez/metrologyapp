from django.db import transaction

from .models import Evento, TipoEvento
from .sanitize import sanear


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
