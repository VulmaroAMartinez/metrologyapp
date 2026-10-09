"""Matriz de permisos rol × acción × método.

La tabla esperada está escrita a mano aquí, copiada de docs/DEFINICION.md §3, y NO importa
nada del código bajo prueba: si `ACCIONES_POR_ROL` se equivoca, la prueba no se "autoaprueba".

§3 (Sí / No):                                                  Admin  Técnico  Espectador
  Consultar instrumentos, calibraciones, certificados,
    timeline, dashboard, calendario y reportes                  Sí      Sí       Sí
  Crear y editar instrumentos, cambiar estado o área, dar de
    baja, registrar calibraciones, subir certificados,
    gestionar reemplazos                                        Sí      Sí       No
  Crear, editar y cancelar avisos                               Sí      Sí       No
  Eliminar certificados (con motivo)                            Sí      No       No
  Ver la bitácora de auditoría completa                         Sí      No       No
  Gestionar usuarios                                            Sí      No       No
  Gestionar catálogos y configuración [PROPUESTA]               Sí      No       No
  Cambiar la propia contraseña (decisión de Vul, no está en §3) Sí      No       No
  Recibir correos de caducidad                                  Sí      Sí       No
"""

import logging

import pytest

from apps.accounts.models import Usuario
from apps.accounts.permissions import ACCIONES_POR_ROL, Accion, puede, requiere
from apps.audit.models import Evento

from .conftest import Api
from .factories import UsuarioFactory
from .urls_probe import METODOS, ruta_de

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.accounts.tests.urls_probe")]

ROLES = ("ADMIN", "TECNICO", "ESPECTADOR")

# acción: (ADMIN, TECNICO, ESPECTADOR)
MATRIZ_SECCION_3 = {
    "CONSULTAR": (True, True, True),
    "GENERAR_REPORTES": (True, True, True),  # §3 los une a la consulta; §7: los 3 roles
    "ESCRIBIR_INSTRUMENTOS": (True, True, False),
    "GESTIONAR_AVISOS": (True, True, False),
    "ELIMINAR_CERTIFICADO": (True, False, False),
    "VER_BITACORA": (True, False, False),
    "GESTIONAR_USUARIOS": (True, False, False),
    "GESTIONAR_CATALOGOS": (True, False, False),
    "CAMBIAR_PROPIA_CONTRASENA": (True, False, False),  # solo el ADMIN asigna contraseñas
    "RECIBIR_CORREOS_CADUCIDAD": (True, True, False),
}
CASOS = [
    (accion, rol, permitido)
    for accion, columnas in MATRIZ_SECCION_3.items()
    for rol, permitido in zip(ROLES, columnas, strict=True)
]
ESCRITURAS = ["POST", "PUT", "PATCH", "DELETE"]


def accion_de(nombre: str) -> Accion:
    return Accion(nombre)


def sesion(rol: str, **extra) -> Api:
    usuario = UsuarioFactory(correo=f"{rol.lower()}@empresa.test", rol=rol, **extra)
    api = Api()
    assert api.login(usuario.correo).status_code == 200
    return api


# --- la tabla de código coincide con §3 ------------------------------------------------------


def test_every_action_in_the_code_is_in_the_expected_table_and_vice_versa():
    assert {a.value for a in Accion} == set(MATRIZ_SECCION_3)


@pytest.mark.parametrize(("accion", "rol", "permitido"), CASOS)
def test_role_action_table_matches_section_3(accion, rol, permitido):
    assert puede(rol, accion_de(accion)) is permitido
    assert (accion_de(accion) in ACCIONES_POR_ROL[rol]) is permitido


def test_the_table_has_exactly_the_three_roles_and_no_extra_actions():
    assert set(ACCIONES_POR_ROL) == set(ROLES) == set(Usuario.Rol.values)
    for indice, rol in enumerate(ROLES):
        esperadas = {a for a, cols in MATRIZ_SECCION_3.items() if cols[indice]}
        assert {a.value for a in ACCIONES_POR_ROL[rol]} == esperadas, rol


def test_the_table_is_immutable_and_unknown_roles_have_no_permissions():
    with pytest.raises(TypeError):
        ACCIONES_POR_ROL["ADMIN"] = frozenset()
    with pytest.raises(AttributeError):
        ACCIONES_POR_ROL["ADMIN"].add(Accion.CONSULTAR)
    assert puede("SUPERUSUARIO", Accion.CONSULTAR) is False
    assert puede(None, Accion.CONSULTAR) is False


def test_requiere_only_accepts_actions():
    with pytest.raises(TypeError):
        requiere("CONSULTAR")
    assert requiere(Accion.VER_BITACORA).accion_requerida is Accion.VER_BITACORA


# --- rol × acción × método sobre vistas de prueba --------------------------------------------


@pytest.mark.parametrize("metodo", METODOS)
@pytest.mark.parametrize(("accion", "rol", "permitido"), CASOS)
def test_endpoint_matrix_role_action_method(accion, rol, permitido, metodo):
    api = sesion(rol)

    respuesta = api.call(metodo, "/" + ruta_de(accion_de(accion)))

    if permitido:
        assert respuesta.status_code == 200, respuesta.content
        assert respuesta.json() == {"ok": True}
    else:
        assert respuesta.status_code == 403
        assert respuesta.json()["codigo"] == "permiso_denegado"
        assert set(respuesta.json()) == {"codigo", "detalle"}


@pytest.mark.parametrize("metodo", METODOS)
@pytest.mark.parametrize("accion", sorted(MATRIZ_SECCION_3))
def test_anonymous_gets_401_on_every_action_endpoint(accion, metodo):
    api = Api()
    api.csrf()

    respuesta = api.call(metodo, "/" + ruta_de(accion_de(accion)))

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "no_autenticado"
    assert respuesta["WWW-Authenticate"] == "Session"


# --- los permisos se verifican en el backend --------------------------------------------------


@pytest.mark.parametrize("metodo", ESCRITURAS)
@pytest.mark.parametrize(
    "accion", sorted(a for a, (_, _, espectador) in MATRIZ_SECCION_3.items() if not espectador)
)
def test_spectator_with_valid_session_and_csrf_cannot_write(accion, metodo):
    api = sesion("ESPECTADOR")
    assert api.token  # token CSRF válido, de la sesión actual

    respuesta = api.call(metodo, "/" + ruta_de(accion_de(accion)))

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "permiso_denegado"  # no es un rechazo de CSRF


def test_hiding_buttons_is_not_enough_a_spectator_cannot_delete_certificates_even_with_csrf():
    api = sesion("ESPECTADOR")

    respuesta = api.call("DELETE", "/" + ruta_de(Accion.ELIMINAR_CERTIFICADO))

    assert respuesta.status_code == 403


# --- usuarios inactivos y desactivados ---------------------------------------------------------


@pytest.mark.parametrize("rol", ROLES)
def test_inactive_users_cannot_authenticate(rol):
    usuario = UsuarioFactory(correo="inactivo@empresa.test", rol=rol, activo=False)
    api = Api()

    assert api.login(usuario.correo).status_code == 401
    assert api.call("GET", "/" + ruta_de(Accion.CONSULTAR)).status_code == 401


@pytest.mark.parametrize("rol", ROLES)
def test_sessions_of_deactivated_users_stop_authenticating(rol):
    api = sesion(rol)
    assert api.call("GET", "/" + ruta_de(Accion.CONSULTAR)).status_code == 200

    usuario = Usuario.objects.get(correo=f"{rol.lower()}@empresa.test")
    usuario.activo = False
    usuario.save()

    respuesta = api.call("GET", "/" + ruta_de(Accion.CONSULTAR))
    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "no_autenticado"


def test_a_role_downgrade_takes_effect_on_the_next_request():
    api = sesion("ADMIN")
    assert api.call("GET", "/" + ruta_de(Accion.VER_BITACORA)).status_code == 200

    Usuario.objects.filter(correo="admin@empresa.test").update(rol="ESPECTADOR")

    assert api.call("GET", "/" + ruta_de(Accion.VER_BITACORA)).status_code == 403


# --- vista sin permisos declarados -------------------------------------------------------------


@pytest.mark.parametrize("metodo", METODOS)
@pytest.mark.parametrize("rol", ROLES)
def test_a_view_that_declares_no_permissions_is_denied_for_every_role(rol, metodo):
    respuesta = sesion(rol).call(metodo, "/api/v1/probe/sin-permisos/")

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "permiso_denegado"


def test_a_view_that_declares_no_permissions_is_401_for_anonymous():
    api = Api()
    api.csrf()

    assert api.call("GET", "/api/v1/probe/sin-permisos/").status_code == 401


# --- forma estándar de los errores -------------------------------------------------------------


def test_standard_error_shapes(api):
    sin_sesion = api.get("/api/v1/auth/me/")
    metodo = api.get("/api/v1/auth/login/")
    invalido = api.post("/api/v1/auth/login/", {})

    assert (sin_sesion.status_code, sin_sesion.json()["codigo"]) == (401, "no_autenticado")
    assert (metodo.status_code, metodo.json()["codigo"]) == (405, "metodo_no_permitido")
    assert (invalido.status_code, invalido.json()["codigo"]) == (400, "datos_invalidos")
    for respuesta in (sin_sesion, metodo, invalido):
        assert set(respuesta.json()) == {"codigo", "detalle"}
    assert "correo" in invalido.json()["detalle"]


# --- denegaciones: sin evento, solo una línea de log sin datos sensibles ------------------------


def test_permission_denials_write_no_event_only_a_log_line(caplog):
    api = sesion("ESPECTADOR")
    eventos_antes = Evento.objects.count()
    caplog.set_level(logging.INFO)

    respuesta = api.call("GET", "/" + ruta_de(Accion.VER_BITACORA))

    assert respuesta.status_code == 403
    assert Evento.objects.count() == eventos_antes  # la denegación no generó evento
    lineas = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("Permiso denegado")
    ]
    assert len(lineas) == 1
    assert "rol=ESPECTADOR" in lineas[0] and "accion=VER_BITACORA" in lineas[0]
    assert "metodo=GET" in lineas[0] and ruta_de(Accion.VER_BITACORA) in lineas[0]
    for sensible in ("espectador@empresa.test", api.session_cookie, api.token, "argon2", "md5$"):
        assert sensible not in lineas[0]


def test_allowed_requests_log_no_denial(caplog):
    api = sesion("ADMIN")
    caplog.set_level(logging.INFO)

    api.call("GET", "/" + ruta_de(Accion.VER_BITACORA))

    assert not [r for r in caplog.records if "Permiso denegado" in r.getMessage()]
