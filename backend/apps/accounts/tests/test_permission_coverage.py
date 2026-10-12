"""Cobertura de la regla «toda vista declara su permiso y toda ruta está en la matriz».

Recorre TODAS las rutas registradas en el URLconf real. Falla si:
- una vista DRF no declara permisos (quedaría en `DenegarPorDefecto`);
- una ruta existe pero no está en `REGISTRO` (cada sprint nuevo debe registrar sus endpoints);
- `REGISTRO` lista una ruta que ya no existe, o sus métodos / su política no coinciden con lo
  que la vista realmente declara.

Además ejercita cada ruta registrada por HTTP con anónimo y con los tres roles.
"""

import importlib

import pytest
from django.urls import get_resolver
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from apps.accounts.permissions import Accion, DenegarPorDefecto, RequiereAccion
from apps.core.testing import Api, UsuarioFactory

from .ayudantes import MATRIZ_SECCION_3, ROLES

pytestmark = pytest.mark.django_db

PUBLICO, AUTENTICADO = "publico", "autenticado"

# Matriz de endpoints REALES. Cada sprint agrega aquí sus rutas: (métodos, política) donde la
# política es PUBLICO, AUTENTICADO o el nombre de una Accion.
REGISTRO = {
    "api/v1/auth/csrf/": ({"GET"}, PUBLICO),
    "api/v1/auth/login/": ({"POST"}, PUBLICO),
    "api/v1/auth/logout/": ({"POST"}, PUBLICO),
    "api/v1/auth/me/": ({"GET"}, AUTENTICADO),
    "api/v1/auth/cambiar-contrasena/": ({"POST"}, "CAMBIAR_PROPIA_CONTRASENA"),  # solo ADMIN
    "api/v1/health/": ({"GET"}, PUBLICO),
    # Gestión de usuarios (commit 8): solo ADMIN.
    "api/v1/usuarios/": ({"GET", "POST"}, "GESTIONAR_USUARIOS"),
    "api/v1/usuarios/<int:pk>/": ({"GET", "PATCH"}, "GESTIONAR_USUARIOS"),
    "api/v1/usuarios/<int:pk>/cambiar-rol/": ({"POST"}, "GESTIONAR_USUARIOS"),
    "api/v1/usuarios/<int:pk>/desactivar/": ({"POST"}, "GESTIONAR_USUARIOS"),
    "api/v1/usuarios/<int:pk>/reactivar/": ({"POST"}, "GESTIONAR_USUARIOS"),
    "api/v1/usuarios/<int:pk>/restablecer-contrasena/": ({"POST"}, "GESTIONAR_USUARIOS"),
    # Bitácora (commit 9): solo lectura, solo ADMIN.
    "api/v1/bitacora/eventos/": ({"GET"}, "VER_BITACORA"),
    "api/v1/schema/": ({"GET"}, PUBLICO),  # vista de Django (no DRF): solo se sirve en desarrollo
}
# Vistas que no son DRF y por tanto no pueden declarar permission_classes. Revisión manual.
VISTAS_NO_DRF = {"api/v1/schema/"}

_METODOS_HTTP = ("get", "post", "put", "patch", "delete")


def rutas(patrones, prefijo=""):
    for patron in patrones:
        texto = prefijo + str(patron.pattern)
        if hasattr(patron, "url_patterns"):
            yield from rutas(patron.url_patterns, texto)
        else:
            yield texto, patron.callback


def declara_permisos(clase) -> bool:
    return list(clase.permission_classes) != [DenegarPorDefecto]


def metodos_de(clase) -> set[str]:
    return {m.upper() for m in _METODOS_HTTP if hasattr(clase, m)}


def politica_de(clase):
    declaradas = list(clase.permission_classes)
    if declaradas == [AllowAny]:
        return PUBLICO
    if declaradas == [IsAuthenticated]:
        return AUTENTICADO
    acciones = [p.accion_requerida.value for p in declaradas if issubclass(p, RequiereAccion)]
    return (
        acciones[0] if len(acciones) == 1 and len(declaradas) == 1 else f"desconocida:{declaradas}"
    )


def auditar(patrones, registro, no_drf):
    """Devuelve la lista de problemas; vacía si todo está declarado y registrado."""
    problemas, vistas = [], dict(rutas(patrones))
    for ruta, callback in vistas.items():
        clase = getattr(callback, "cls", None)
        if clase is None or not issubclass(clase, APIView):
            if ruta not in no_drf:
                problemas.append(f"{ruta}: no es una vista DRF y no está en VISTAS_NO_DRF")
            continue
        if not declara_permisos(clase):
            problemas.append(f"{ruta}: la vista no declara permisos (queda denegada)")
        if ruta not in registro:
            problemas.append(f"{ruta}: no está en la matriz de pruebas (REGISTRO)")
            continue
        metodos, politica = registro[ruta]
        if metodos != metodos_de(clase):
            problemas.append(f"{ruta}: métodos {metodos_de(clase)} distintos de los registrados")
        if politica != politica_de(clase):
            problemas.append(f"{ruta}: política {politica_de(clase)} distinta de la registrada")
    for ruta in registro:
        if ruta not in vistas:
            problemas.append(f"{ruta}: está en REGISTRO pero ya no existe como ruta")
    return problemas


def test_every_real_route_declares_its_permission_and_is_in_the_matrix():
    assert auditar(get_resolver().url_patterns, REGISTRO, VISTAS_NO_DRF) == []


def test_the_registry_is_not_empty_and_covers_all_auth_routes():
    encontradas = {ruta for ruta, _ in rutas(get_resolver().url_patterns)}

    assert encontradas == set(REGISTRO)
    assert {r for r in encontradas if r.startswith("api/v1/auth/")} >= {
        "api/v1/auth/login/",
        "api/v1/auth/logout/",
        "api/v1/auth/me/",
    }


# --- el verificador detecta los incumplimientos (si no, la prueba anterior no probaría nada) ----


def test_the_checker_flags_a_view_without_permissions_and_an_unregistered_route():
    probe = importlib.import_module("apps.accounts.tests.urls_probe")

    problemas = auditar(probe.urlpatterns, REGISTRO, VISTAS_NO_DRF)

    assert any("probe/sin-permisos/: la vista no declara permisos" in p for p in problemas)
    assert any("probe/consultar/: no está en la matriz" in p for p in problemas)
    assert any("api/v1/health/: está en REGISTRO pero ya no existe" in p for p in problemas)


def test_the_checker_flags_wrong_methods_and_wrong_policy():
    registro = dict(REGISTRO)
    registro["api/v1/auth/me/"] = ({"GET", "POST"}, PUBLICO)

    problemas = auditar(get_resolver().url_patterns, registro, VISTAS_NO_DRF)

    assert any("auth/me/: métodos" in p for p in problemas)
    assert any("auth/me/: política" in p for p in problemas)


def test_the_checker_flags_a_non_drf_view_that_is_not_allowed():
    problemas = auditar(get_resolver().url_patterns, REGISTRO, set())

    assert any("schema/: no es una vista DRF" in p for p in problemas)


# --- cada ruta registrada, por HTTP, con anónimo y con los tres roles ---------------------


def peticion(api, ruta, metodo):
    # login con cuerpo vacío -> 400 (validación) y cambiar-contrasena -> 400: no son 401/403.
    # Las rutas con parámetro se prueban con un id inexistente: el permiso se evalúa antes de
    # buscar el recurso, así que un rol sin permiso siempre recibe 403 (nunca 404).
    return api.call(metodo, "/" + ruta.replace("<int:pk>", "987654"))


def esperado(politica, rol):
    """(permitido?) según la política registrada y la tabla de §3 escrita en la otra prueba."""
    if politica in (PUBLICO, AUTENTICADO):
        return rol is not None or politica == PUBLICO
    columnas = MATRIZ_SECCION_3[politica]
    return rol is not None and columnas[ROLES.index(rol)]


CASOS_HTTP = [
    (ruta, metodo, politica, actor)
    for ruta, (metodos, politica) in REGISTRO.items()
    for metodo in sorted(metodos)
    for actor in (None, *ROLES)
]


@pytest.mark.parametrize(
    ("ruta", "metodo", "politica", "actor"),
    CASOS_HTTP,
    ids=[f"{r}-{m}-{a or 'anonimo'}" for r, m, _, a in CASOS_HTTP],
)
def test_registered_endpoints_follow_their_policy(ruta, metodo, politica, actor):
    if actor is None:
        api = Api()
        api.csrf()
    else:
        usuario = UsuarioFactory(correo=f"{actor.lower()}@empresa.test", rol=actor)
        api = Api()
        assert api.login(usuario.correo).status_code == 200

    respuesta = peticion(api, ruta, metodo)

    if esperado(politica, actor):
        assert respuesta.status_code not in (401, 403), respuesta.content
    elif actor is None:
        assert respuesta.status_code == 401
    else:
        assert respuesta.status_code == 403


def test_accion_enum_matches_the_registry_vocabulary():
    # Una política de REGISTRO que sea una acción debe existir en el enum.
    for _, politica in REGISTRO.values():
        assert politica in (PUBLICO, AUTENTICADO) or politica in {a.value for a in Accion}
