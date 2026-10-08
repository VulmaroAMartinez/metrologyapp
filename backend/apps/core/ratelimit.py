"""Límite de tasa de ventana fija, compartido entre workers mediante la caché en base de datos.

`cache.incr` no es atómico (lee y escribe), así que cada intento toma una ranura con
`cache.add`, que sí lo es: gana quien inserta primero la clave. Con `limit` ranuras por
ventana, el intento `limit + 1` no encuentra ranura libre y se rechaza.
"""

import hashlib
from dataclasses import dataclass

from django.core.cache import cache
from django.utils import timezone


@dataclass(frozen=True)
class RateResult:
    allowed: bool
    retry_after: int  # segundos hasta la próxima ventana; 0 si se permitió


def check_rate(scope: str, identifier: str, limit: int, window: int = 60) -> RateResult:
    now = timezone.now().timestamp()
    start = int(now // window)
    retry_after = max(1, window - int(now % window))
    # El identificador (correo, IP) va hasheado: la tabla de caché no guarda datos personales.
    digest = hashlib.sha256(f"{scope}:{identifier}".encode()).hexdigest()
    for slot in range(limit):
        if cache.add(f"rl:{digest}:{start}:{slot}", 1, timeout=retry_after + 1):
            return RateResult(True, 0)
    return RateResult(False, retry_after)
