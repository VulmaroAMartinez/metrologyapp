import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

from apps.core.ratelimit import check_rate

pytestmark = pytest.mark.django_db


def test_allows_exactly_limit_attempts_then_blocks(clock):
    results = [check_rate("login", "k", limit=5) for _ in range(7)]

    assert [r.allowed for r in results] == [True] * 5 + [False] * 2


def test_blocked_result_reports_seconds_until_next_window(clock):
    for _ in range(3):
        check_rate("login", "k", limit=3)

    blocked = check_rate("login", "k", limit=3)

    assert blocked.allowed is False
    assert blocked.retry_after == 30  # el reloj está en el segundo 30 del minuto


def test_window_rolls_over(clock):
    for _ in range(3):
        check_rate("login", "k", limit=3)
    assert not check_rate("login", "k", limit=3).allowed

    clock.advance(seconds=31)

    assert check_rate("login", "k", limit=3).allowed


def test_identifiers_and_scopes_are_independent(clock):
    for _ in range(3):
        check_rate("login", "a", limit=3)

    assert check_rate("login", "b", limit=3).allowed
    assert check_rate("otro", "a", limit=3).allowed


def test_zero_limit_always_blocks(clock):
    assert not check_rate("login", "k", limit=0).allowed


@pytest.mark.django_db(transaction=True)
def test_counter_is_shared_and_atomic_across_connections(clock):
    """20 hilos (cada uno con su conexión, como los workers) compiten por 5 ranuras. Reloj
    controlado: con el reloj real, un cambio de hora a mitad de la prueba partiría la ventana."""

    def attempt(_):
        try:
            return check_rate("concurrent", "k", limit=5, window=3600).allowed
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        allowed = list(pool.map(attempt, range(20)))

    assert allowed.count(True) == 5


# --- el identificador va protegido con HMAC(SECRET_KEY), no con un hash sin clave ------------


def digests_en_cache() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT cache_key FROM django_cache")
        claves = [fila[0] for fila in cursor.fetchall()]
    return {m.group(1) for k in claves if (m := re.search(r"rl:([0-9a-f]{64}):", k))}


def test_the_cache_key_is_not_a_plain_hash_of_the_identifier(clock):
    check_rate("login", "ana@empresa.test|203.0.113.7", limit=3)

    sin_clave = hashlib.sha256(b"login:ana@empresa.test|203.0.113.7").hexdigest()

    assert len(digests_en_cache()) == 1
    assert sin_clave not in digests_en_cache()  # un diccionario de correos/IP no lo revierte


def test_the_cache_key_depends_on_the_secret_key(clock, settings):
    check_rate("login", "k", limit=3)
    settings.SECRET_KEY = "otra-clave-secreta-" + "x" * 40

    check_rate("login", "k", limit=3)

    assert len(digests_en_cache()) == 2


def test_the_same_secret_key_gives_the_same_key(clock):
    check_rate("login", "k", limit=3)
    check_rate("login", "k", limit=3)

    assert len(digests_en_cache()) == 1
