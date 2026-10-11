"""Errores unificados: ErrorDeUsuario es una APIException y todo `datos_invalidos` lleva `campos`.

El inicio de sesión fallido es la excepción deliberada: NO lanza (el contador y SESION_FALLIDA se
confirman con la transacción y la respuesta es 401).
"""

import ast
from pathlib import Path

import pytest
from django.db import connection
from rest_framework.exceptions import APIException

from apps.accounts import services
from apps.accounts.models import Usuario
from apps.audit.models import Evento
from apps.core.testing import DEFAULT_PASSWORD, Api, UsuarioFactory, en_hilo

from .ayudantes import CASOS

BACKEND = Path(__file__).resolve().parents[3]
BASE = "/api/v1/usuarios/"
CLAVE = "tornillo-azul-77-nube"


# --- la jerarquía ---------------------------------------------------------------------------


def test_every_business_error_is_an_api_exception_with_its_http_status():
    errores = [
        clase
        for clase in vars(services).values()
        if isinstance(clase, type) and issubclass(clase, services.ErrorDeUsuario)
    ]

    assert len(errores) >= 12
    for clase in errores:
        assert issubclass(clase, APIException)
        instancia = clase()
        assert instancia.status_code == clase.estado
        assert str(instancia) == instancia.detalle
        assert instancia.detail["codigo"] == clase.codigo


def test_the_password_errors_are_datos_invalidos_with_their_field():
    actual = services.ContrasenaActualIncorrecta()
    repetida = services.ContrasenaRepetida()

    assert isinstance(actual, services.DatosDeUsuarioInvalidos)
    assert isinstance(repetida, services.DatosDeUsuarioInvalidos)
    assert (actual.codigo, actual.estado, list(actual.campos)) == (
        "datos_invalidos",
        400,
        ["contrasena_actual"],
    )
    assert list(repetida.campos) == ["contrasena_nueva"]


def test_the_views_do_not_catch_business_errors_any_more():
    for modulo in ("views.py", "views_usuarios.py"):
        arbol = ast.parse((BACKEND / "apps/accounts" / modulo).read_text(encoding="utf-8"))
        capturas = [
            ast.unparse(n.type)
            for n in ast.walk(arbol)
            if isinstance(n, ast.ExceptHandler) and n.type is not None
        ]
        assert not [c for c in capturas if "Error" in c], (modulo, capturas)


# --- el sobre {codigo, detalle[, campos]} en los endpoints de administración ------------------


def cuerpo(**extra):
    return {
        "nombre": "Luis",
        "correo": "luis@empresa.test",
        "rol": "TECNICO",
        "contrasena": CLAVE,
        **extra,
    }


@pytest.mark.parametrize(
    ("extra", "campo"),
    [
        ({"nombre": "   "}, "nombre"),
        ({"correo": "no-es-un-correo"}, "correo"),
        ({"rol": "DIOS"}, "rol"),
        ({"contrasena": "qwertyuiop"}, "contrasena"),
        ({"numero_nomina": "Ñ-1"}, "numero_nomina"),
    ],
)
def test_field_errors_of_the_admin_endpoints_name_their_field(api_ana, extra, campo):
    respuesta = api_ana.post(BASE, cuerpo(**extra))

    assert respuesta.status_code == 400
    sobre = respuesta.json()
    assert sobre["codigo"] == "datos_invalidos"
    assert isinstance(sobre["detalle"], str)
    assert list(sobre["campos"]) == [campo]
    assert all(isinstance(m, str) for m in sobre["campos"][campo])


def test_a_business_error_without_a_field_has_no_campos(api_ana):
    otra = UsuarioFactory(correo="otra@empresa.test")
    api_ana.post(BASE, cuerpo())

    duplicado = api_ana.post(BASE, cuerpo())
    inexistente = api_ana.post(f"{BASE}999999/desactivar/")

    assert (duplicado.status_code, duplicado.json()["codigo"]) == (400, "correo_duplicado")
    assert set(duplicado.json()) == {"codigo", "detalle"}
    assert (inexistente.status_code, inexistente.json()["codigo"]) == (404, "no_encontrado")
    assert otra.activo


def test_cambiar_rol_and_editar_use_the_same_field_shape(api_ana):
    objetivo = UsuarioFactory(correo="b@empresa.test")

    rol = api_ana.post(f"{BASE}{objetivo.pk}/cambiar-rol/", {"rol": "DIOS"})
    nombre = api_ana.call("PATCH", f"{BASE}{objetivo.pk}/", {"nombre": "x" * 200})

    for respuesta, campo in ((rol, "rol"), (nombre, "nombre")):
        assert respuesta.status_code == 400
        assert respuesta.json()["codigo"] == "datos_invalidos"
        assert list(respuesta.json()["campos"]) == [campo]


# --- un inicio de sesión fallido NO lanza y se confirma ----------------------------------------


@pytest.mark.parametrize("caso", list(CASOS))
def test_a_failed_login_returns_a_result_and_never_raises(caso, cuentas, relaxed_rate):
    correo, contrasena = CASOS[caso]

    resultado = services.iniciar_sesion(None, correo, contrasena)

    assert resultado.usuario is None
    assert resultado.motivo is not None


def test_iniciar_sesion_has_no_raise_of_business_errors():
    arbol = ast.parse((BACKEND / "apps/accounts/services.py").read_text(encoding="utf-8"))
    funcion = next(
        n for n in ast.walk(arbol) if isinstance(n, ast.FunctionDef) and n.name == "iniciar_sesion"
    )
    lanzamientos = [n for n in ast.walk(funcion) if isinstance(n, ast.Raise)]

    # Solo el `raise` pelado que reenvía errores de base de datos que no son de candado.
    assert [n.exc for n in lanzamientos] == [None]


@pytest.mark.django_db(transaction=True)
def test_the_failed_login_counter_and_event_are_committed_with_the_401(relaxed_rate):
    """Transacciones reales: otra conexión ve el contador y el evento tras la respuesta 401."""
    usuario = UsuarioFactory(correo="ana@empresa.test")

    respuesta = Api().login(usuario.correo, "no-es-la-clave-1")

    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "credenciales_invalidas"

    def desde_otra_conexion():
        try:
            fresco = Usuario.objects.get(pk=usuario.pk)
            eventos = Evento.objects.filter(tipo="SESION_FALLIDA", usuario=usuario).count()
            return fresco.intentos_fallidos, eventos
        finally:
            connection.close()

    hilo, resultado = en_hilo("otra", desde_otra_conexion)
    hilo.join(10)
    assert resultado["valor"] == (1, 1)


@pytest.mark.django_db(transaction=True)
def test_the_lock_out_is_committed_with_the_401_too(settings, relaxed_rate):
    settings.LOGIN_MAX_FAILED_ATTEMPTS = 2
    usuario = UsuarioFactory(correo="ana@empresa.test")
    api = Api()

    for _ in range(2):
        assert api.login(usuario.correo, "no-es-la-clave-1").status_code == 401

    usuario.refresh_from_db()
    assert usuario.bloqueado_hasta is not None
    tipos = set(Evento.objects.values_list("tipo", flat=True))
    assert {"SESION_FALLIDA", "CUENTA_BLOQUEADA"} <= tipos
    assert Api().login(usuario.correo, DEFAULT_PASSWORD).status_code == 401  # bloqueada
