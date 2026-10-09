"""Rendimiento razonable de la bitácora con 100 000 eventos.

Se siembran con una sola sentencia SQL, se ejecuta ANALYZE y se verifica con EXPLAIN que ninguna
de las consultas hace Seq Scan sobre audit_evento; además se mide el tiempo de punta a punta
(sesión, permisos, consulta y serialización) contra un presupuesto fijo.

PRESUPUESTO_MS: 300 ms por petición (mediana de 5), medido dentro del proceso de pruebas sobre
PostgreSQL real. Es un margen holgado para no ser frágil en CI; en una máquina normal la
primera página tarda decenas de milisegundos. Documentado en docs/decisiones.md.
"""

import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Usuario
from apps.accounts.tests.conftest import Api
from apps.accounts.tests.factories import UsuarioFactory
from apps.audit.models import Evento, TipoEvento
from apps.audit.services import (
    FiltrosEventos,
    codificar_cursor,
    construir_consulta,
    consultar_eventos,
)

pytestmark = pytest.mark.django_db

TOTAL = 100_000
PRESUPUESTO_MS = 300
FIN = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)  # el evento más reciente; cada uno 5 min antes
URL = "/api/v1/bitacora/eventos/"


@pytest.fixture(scope="module")
def cien_mil(django_db_setup, django_db_blocker):
    """100 000 eventos repartidos en ~347 días: 25 tipos, 1/7 del Sistema, 1/3 con instrumento.

    Se siembran UNA vez por módulo (confirmados, fuera de la transacción de cada prueba) y se
    vacía la tabla al terminar. TRUNCATE lo permite el rol dueño de la base de pruebas; el ORM
    no (los eventos son solo de inserción)."""
    with django_db_blocker.unblock():
        usuarios = [UsuarioFactory(correo=f"masivo{i}@empresa.test") for i in range(5)]
        tipos = list(TipoEvento.values)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_evento
                    (ocurrido_en, usuario_id, instrumento_id, tipo, descripcion, antes, despues, ip)
                SELECT %s::timestamptz - (g * interval '5 minutes'),
                       CASE WHEN g %% 7 = 0 THEN NULL ELSE (%s::bigint[])[1 + (g %% 5)] END,
                       CASE WHEN g %% 3 = 0 THEN (g %% 500) + 1 ELSE NULL END,
                       (%s::text[])[1 + (g %% %s)],
                       'Evento ' || g, NULL, NULL, NULL
                FROM generate_series(0, %s) AS g
                """,
                [FIN, [u.pk for u in usuarios], tipos, len(tipos), TOTAL - 1],
            )
            cursor.execute("ANALYZE audit_evento")
        assert Evento.objects.count() == TOTAL
        yield SimpleNamespace(tipos=tipos, usuario_id=usuarios[0].pk)
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE audit_evento")
            cursor.execute("DELETE FROM accounts_usuario WHERE correo LIKE 'masivo%@empresa.test'")


def nodos(plan):
    yield plan
    for hijo in plan.get("Plans", []):
        yield from nodos(hijo)


def plan_de(consulta, limite):
    crudo = consulta[:limite].explain(format="json")
    return json.loads(crudo)[0]["Plan"]


def sin_seq_scan(plan):
    return [
        n
        for n in nodos(plan)
        if n["Node Type"] == "Seq Scan" and n.get("Relation Name") == "audit_evento"
    ]


def indices_usados(plan):
    return {n["Index Name"] for n in nodos(plan) if "Index Name" in n}


# --- los índices se usan: ningún Seq Scan sobre la tabla ---


def test_first_page_uses_the_cursor_index(cien_mil):
    plan = plan_de(construir_consulta(FiltrosEventos()), 26)

    assert sin_seq_scan(plan) == []
    assert "evento_ocurrido_id_idx" in indices_usados(plan)


def test_a_page_with_date_and_type_filters_does_not_scan_the_table(cien_mil):
    filtros = FiltrosEventos(
        desde=(FIN - timedelta(days=60)).date(),
        hasta=(FIN - timedelta(days=30)).date(),
        tipos=(TipoEvento.SESION_FALLIDA,),
    )
    cursor = (FIN - timedelta(days=40), 50_000)

    for cursor_usado in (None, cursor):
        plan = plan_de(construir_consulta(filtros, cursor_usado), 26)

        assert sin_seq_scan(plan) == [], json.dumps(plan)[:600]
        assert indices_usados(plan)  # usa algún índice


@pytest.mark.parametrize(
    "filtros",
    [
        FiltrosEventos(usuario="sistema"),
        FiltrosEventos(instrumento_id=7),
        FiltrosEventos(tipos=(TipoEvento.CUENTA_BLOQUEADA, TipoEvento.SESION_FALLIDA)),
        FiltrosEventos(desde=(FIN - timedelta(days=5)).date()),
        FiltrosEventos(hasta=(FIN - timedelta(days=300)).date()),
    ],
    ids=["sistema", "instrumento", "dos-tipos", "solo-desde", "solo-hasta"],
)
def test_other_filters_do_not_scan_the_table_either(cien_mil, filtros):
    plan = plan_de(construir_consulta(filtros), 26)

    assert sin_seq_scan(plan) == [], json.dumps(plan)[:600]


def test_filtering_by_user_does_not_scan_the_table(cien_mil):
    plan = plan_de(construir_consulta(FiltrosEventos(usuario=cien_mil.usuario_id)), 26)

    assert sin_seq_scan(plan) == [], json.dumps(plan)[:600]


def test_a_deep_page_costs_the_same_as_the_first_one(cien_mil):
    """Sin OFFSET: leer la página 3000 no recorre las 75 000 filas anteriores."""
    profundo = (FIN - timedelta(minutes=5 * 75_000), 25_000)

    plan = plan_de(construir_consulta(FiltrosEventos(), profundo), 26)
    filas_leidas = max(n.get("Actual Rows", 0) for n in nodos(plan))

    assert sin_seq_scan(plan) == []
    assert filas_leidas <= 100
    paginas, siguiente = consultar_eventos(FiltrosEventos(), profundo, 25)
    assert len(paginas) == 25
    assert siguiente


# --- tiempos ---


def mediana_ms(api, ruta, repeticiones=5):
    api.get(ruta)  # calienta
    tiempos = []
    for _ in range(repeticiones):
        inicio = time.perf_counter()
        respuesta = api.get(ruta)
        tiempos.append((time.perf_counter() - inicio) * 1000)
        assert respuesta.status_code == 200
    return statistics.median(tiempos)


@pytest.fixture
def api_admin(db):
    admin = UsuarioFactory(correo="admin@empresa.test", rol=Usuario.Rol.ADMIN)
    api = Api()
    api.client.force_login(admin)
    api.csrf()
    return api


def test_response_times_stay_within_the_budget(cien_mil, api_admin):
    primera = mediana_ms(api_admin, URL)
    desde, hasta = (FIN - timedelta(days=60)).date(), (FIN - timedelta(days=30)).date()
    filtrada = mediana_ms(
        api_admin, f"{URL}?desde={desde}&hasta={hasta}&tipo=SESION_FALLIDA&tamano=100"
    )
    siguiente = api_admin.get(f"{URL}?tamano=100").json()["next"]
    ruta = f"{urlparse(siguiente).path}?{urlparse(siguiente).query}"
    profunda = mediana_ms(api_admin, ruta)

    print(f"\nprimera={primera:.0f} ms, filtrada={filtrada:.0f} ms, con cursor={profunda:.0f} ms")  # noqa: T201
    for nombre, tiempo in (("primera", primera), ("filtrada", filtrada), ("cursor", profunda)):
        assert tiempo < PRESUPUESTO_MS, f"{nombre}: {tiempo:.0f} ms > {PRESUPUESTO_MS} ms"


def test_the_whole_log_can_be_walked_in_pages_without_count_or_offset(cien_mil, api_admin):
    ruta, paginas, vistos = f"{URL}?tamano=100", 0, 0
    sql = ""
    while ruta and paginas < 12:  # 1 200 eventos bastan para comprobar el patrón de consultas
        with CaptureQueriesContext(connection) as consultas:
            cuerpo = api_admin.get(ruta).json()
        sql += " ".join(q["sql"] for q in consultas)
        vistos += len(cuerpo["results"])
        paginas += 1
        siguiente = cuerpo["next"]
        ruta = f"{urlparse(siguiente).path}?{urlparse(siguiente).query}" if siguiente else None

    assert vistos == 1200
    assert paginas == 12
    assert "COUNT(" not in sql.upper()
    assert "OFFSET" not in sql.upper()


def test_cursor_round_trip_matches_the_seeded_order(cien_mil):
    filas, siguiente = consultar_eventos(FiltrosEventos(), None, 10)
    ultima = filas[-1]

    assert siguiente == codificar_cursor(ultima.ocurrido_en, ultima.pk)
    assert [f.ocurrido_en for f in filas] == sorted((f.ocurrido_en for f in filas), reverse=True)
