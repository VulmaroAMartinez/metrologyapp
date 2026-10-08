from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest


class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    """Reloj controlado para sesiones, bloqueos y ventanas de tasa.

    Django lee la hora con `timezone.now()` (sesiones) y con `tz_now`, enlazado al importar,
    en la caché de base de datos: hay que parchear ambos.
    """
    reloj = Clock(datetime(2026, 10, 8, 12, 0, 30, tzinfo=UTC))
    with (
        mock.patch("django.utils.timezone.now", side_effect=lambda: reloj.now),
        mock.patch("django.core.cache.backends.db.tz_now", side_effect=lambda: reloj.now),
    ):
        yield reloj
