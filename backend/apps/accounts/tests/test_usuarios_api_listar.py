"""API de usuarios, lectura: listado (paginación, filtros, búsqueda, orden) y detalle."""

from datetime import timedelta
from urllib.parse import quote

import pytest
from django.utils import timezone

from apps.accounts.models import Usuario
from apps.core.testing import UsuarioFactory

from .ayudantes import BASE, CAMPOS, ESPECTADOR, TECNICO, ruta

pytestmark = pytest.mark.django_db


# --- listado: paginación, filtros, búsqueda y orden ----------------------------------------------


def crear_muchos(n, **extra):
    Usuario.objects.bulk_create(
        [
            Usuario(
                correo=f"masivo{i:03}@empresa.test",
                nombre=f"Masivo {i:03}",
                rol=extra.get("rol", TECNICO),
                password="!",
                activo=extra.get("activo", True),
            )
            for i in range(n)
        ]
    )


def test_list_shape_and_default_page_size(api_admin):
    crear_muchos(30)

    respuesta = api_admin.get(BASE)

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200
    assert set(cuerpo) == {"count", "next", "previous", "results"}
    assert cuerpo["count"] == 31
    assert len(cuerpo["results"]) == 25
    assert cuerpo["next"]
    assert cuerpo["previous"] is None
    assert all(set(fila) == CAMPOS for fila in cuerpo["results"])


def test_a_repeated_filter_parameter_uses_the_last_value(api_admin):
    """Ancla de `request.query_params.dict()`: con `?rol=A&rol=B` rige el ÚLTIMO valor.
    Si se cambia a `getlist` (varios valores) debe ser una decisión de producto, no un accidente."""
    crear_muchos(3, rol=ESPECTADOR)

    respuesta = api_admin.get(f"{BASE}?rol=ADMIN&rol=ESPECTADOR")

    assert respuesta.status_code == 200
    assert {fila["rol"] for fila in respuesta.json()["results"]} == {"ESPECTADOR"}
    assert respuesta.json()["count"] == 3


def test_pagination_walks_through_every_user_exactly_once(api_admin):
    crear_muchos(30)

    ids = []
    for pagina in (1, 2):
        ids += [u["id"] for u in api_admin.get(f"{BASE}?pagina={pagina}").json()["results"]]

    assert len(ids) == 31
    assert len(set(ids)) == 31


def test_page_size_is_configurable_and_capped_at_100(api_admin):
    crear_muchos(110)

    assert len(api_admin.get(f"{BASE}?tamano=10").json()["results"]) == 10
    assert len(api_admin.get(f"{BASE}?tamano=100").json()["results"]) == 100
    assert len(api_admin.get(f"{BASE}?tamano=1000").json()["results"]) == 100  # tope


@pytest.mark.parametrize("tamano", ["0", "-5", "abc", ""])
def test_invalid_page_size_falls_back_to_the_default(api_admin, tamano):
    crear_muchos(30)

    assert len(api_admin.get(f"{BASE}?tamano={tamano}").json()["results"]) == 25


def test_a_page_out_of_range_is_a_clean_404(api_admin):
    respuesta = api_admin.get(f"{BASE}?pagina=99")

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "no_encontrado"


def test_filter_by_role_and_active(api_admin):
    UsuarioFactory(correo="t1@empresa.test", rol=TECNICO)
    UsuarioFactory(correo="t2@empresa.test", rol=TECNICO, activo=False)
    UsuarioFactory(correo="e1@empresa.test", rol=ESPECTADOR)

    def correos(consulta):
        return {u["correo"] for u in api_admin.get(f"{BASE}?{consulta}").json()["results"]}

    assert correos("rol=TECNICO") == {"t1@empresa.test", "t2@empresa.test"}
    assert correos("rol=TECNICO&activo=true") == {"t1@empresa.test"}
    assert correos("activo=false") == {"t2@empresa.test"}
    assert correos("activo=0") == {"t2@empresa.test"}
    assert correos("rol=ADMIN") == {"admin@empresa.test"}
    assert len(correos("")) == 4


@pytest.mark.parametrize(
    "consulta", ["rol=SUPERADMIN", "activo=quizas", "orden=password", "orden=intentos_fallidos"]
)
def test_invalid_filters_are_400_and_cannot_sort_by_sensitive_columns(api_admin, consulta):
    respuesta = api_admin.get(f"{BASE}?{consulta}")

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "datos_invalidos"


def test_search_by_name_email_and_nomina_ignoring_case(api_admin):
    UsuarioFactory(correo="lucia@empresa.test", nombre="Lucía Gómez", numero_nomina="N-0042")
    UsuarioFactory(correo="otro@empresa.test", nombre="Otro Nombre")

    def buscar(texto):
        respuesta = api_admin.get(f"{BASE}?buscar={quote(texto)}")
        return {u["correo"] for u in respuesta.json()["results"]}

    assert buscar("GÓMEZ") == {"lucia@empresa.test"}
    assert buscar("lucia@") == {"lucia@empresa.test"}
    assert buscar("n-0042") == {"lucia@empresa.test"}
    assert buscar("no-existe") == set()
    assert buscar("otro") == {"otro@empresa.test"}


def test_search_treats_wildcards_literally(api_admin):
    UsuarioFactory(correo="a@empresa.test", nombre="Ana")

    assert api_admin.get(f"{BASE}?buscar=%25").json()["count"] == 0  # "%" no es comodín


def test_ordering(api_admin):
    UsuarioFactory(correo="b@empresa.test", nombre="Beto")
    UsuarioFactory(correo="a@empresa.test", nombre="Zeta")

    def nombres(orden):
        return [u["nombre"] for u in api_admin.get(f"{BASE}?orden={orden}").json()["results"]]

    assert nombres("nombre") == sorted(nombres("nombre"))
    assert nombres("-nombre") == sorted(nombres("-nombre"), reverse=True)
    assert nombres("correo")[0] == "Zeta"  # a@empresa.test va antes que admin@ y b@
    assert api_admin.get(f"{BASE}?orden=-creado_en").status_code == 200


# --- detalle y forma de las respuestas ---


def test_detail_shows_the_public_fields_and_read_only_lock_date(api_admin):
    bloqueo = timezone.now() + timedelta(minutes=10)
    objetivo = UsuarioFactory(
        correo="b@empresa.test", intentos_fallidos=4, bloqueado_hasta=bloqueo, numero_nomina="X1"
    )

    respuesta = api_admin.get(ruta(objetivo.pk))

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200
    assert set(cuerpo) == CAMPOS
    assert cuerpo["bloqueado_hasta"] is not None
    assert cuerpo["numero_nomina"] == "X1"
    assert "intentos_fallidos" not in cuerpo
    assert "password" not in cuerpo
    assert respuesta["Cache-Control"] == "no-store"


def test_detail_of_an_unknown_user_is_404(api_admin):
    respuesta = api_admin.get(ruta(987654))

    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "no_encontrado"
