"""Constantes y ayudantes de las pruebas de accounts que usa más de un archivo.

(Las fixtures comunes de varias apps están en el `conftest.py` raíz y los ayudantes generales
en `apps/core/testing.py`; esto es solo de accounts.)
"""

import json
import threading
from contextlib import contextmanager
from unittest import mock

from django.contrib.auth.hashers import check_password

from apps.accounts import services
from apps.accounts.models import Usuario
from apps.accounts.services import autenticacion, usuarios
from apps.audit.models import Evento
from apps.core.testing import DEFAULT_PASSWORD, TOPE, Api

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

# Un caso por cada razón por la que un login puede fallar: nombre -> (correo, contraseña).
CASOS = {
    "correo inexistente": ("nadie@empresa.test", DEFAULT_PASSWORD),
    "contraseña incorrecta": ("activo@empresa.test", "otra-contrasena-1"),
    "usuario inactivo": ("inactivo@empresa.test", DEFAULT_PASSWORD),
    "cuenta bloqueada (contraseña correcta)": ("bloqueado@empresa.test", DEFAULT_PASSWORD),
    "sin contraseña utilizable": ("sinclave@empresa.test", DEFAULT_PASSWORD),
}


# --- puntos de inyección (en vez de mock.patch sobre atributos internos de services) ----------


def lanza(excepcion):
    """Función que lanza `excepcion` cuando se la llama (para sustituir un punto de inyección)."""

    def _lanzar(*args, **kwargs):
        raise excepcion

    return _lanzar


@contextmanager
def evento_que_falla(error=None):
    """El registro de eventos (de las operaciones de usuarios Y del login) lanza un error."""
    error = error or RuntimeError("falla")
    with (
        usuarios.sustituir(registrar_evento=lanza(error)),
        autenticacion.sustituir(registrar_evento=lanza(error)),
    ):
        yield


def sin_comprobar_duplicados():
    """Simula que dos envíos simultáneos pasan la comprobación previa de duplicados."""
    return usuarios.sustituir(verificar_duplicados=lambda *args, **kwargs: None)


def sin_limpieza():
    """El borrado de sesiones (limpieza posterior al commit) no hace nada."""
    return usuarios.sustituir(cerrar_sesiones=lambda usuario_pk: 0)


def limpieza_que_falla():
    return usuarios.sustituir(cerrar_sesiones=lanza(RuntimeError("detalle-interno-xyz")))


@contextmanager
def espiar_hash():
    """Cuenta las verificaciones de hash del login (`call_count`, `call_args`) sin cambiarlas."""
    espia = mock.Mock(wraps=check_password)
    with autenticacion.sustituir(verificar_hash=espia):
        yield espia


@contextmanager
def retener(punto, hilo, retencion, *, despues=False, modulo=usuarios):
    """Detiene al hilo `hilo` en el punto de inyección `punto` de `modulo` (antes de llamarlo, o
    después con `despues`). Los demás hilos pasan sin esperar."""
    real = getattr(modulo.puntos, punto)

    def envoltura(*args, **kwargs):
        if threading.current_thread().name != hilo:
            return real(*args, **kwargs)
        if not despues:
            retencion.dentro.set()
            assert retencion.seguir.wait(TOPE)
        resultado = real(*args, **kwargs)
        if despues:
            retencion.dentro.set()
            assert retencion.seguir.wait(TOPE)
        return resultado

    with modulo.sustituir(**{punto: envoltura}):
        yield


# --- gestión de usuarios: constantes y ayudantes de varias pruebas ------------------------------

BASE = "/api/v1/usuarios/"
CLAVE = "tornillo-azul-77-nube"
CLAVE_2 = "nube-de-algodon-88-azul"
ADMIN, TECNICO, ESPECTADOR = Usuario.Rol.ADMIN, Usuario.Rol.TECNICO, Usuario.Rol.ESPECTADOR
CAMPOS = {
    "id",
    "nombre",
    "correo",
    "numero_nomina",
    "rol",
    "activo",
    "creado_en",
    "bloqueado_hasta",
}


def ruta(pk, accion=""):
    return f"{BASE}{pk}/{accion + '/' if accion else ''}"


def sesion(usuario):
    api = Api()
    assert api.login(usuario.correo).status_code == 200
    return api


def valores(objeto):
    """Todas las claves y valores de un JSON, a cualquier profundidad."""
    if isinstance(objeto, dict):
        for clave, valor in objeto.items():
            yield clave
            yield from valores(valor)
    elif isinstance(objeto, list):
        for elemento in objeto:
            yield from valores(elemento)
    else:
        yield objeto


def crear(actor, **extra):
    datos = {
        "nombre": "Nuevo",
        "correo": "nuevo@empresa.test",
        "rol": TECNICO,
        "contrasena": CLAVE,
        **extra,
    }
    return services.crear_usuario(actor.pk, **datos)


def instantanea():
    """Estado completo de usuarios y eventos, para comprobar que nada cambió."""
    usuarios = list(Usuario.objects.order_by("pk").values())
    return json.dumps(usuarios, default=str), Evento.objects.count()


def volcado_eventos():
    return json.dumps(list(Evento.objects.values()), default=str, ensure_ascii=False)


def nuevo(**extra):
    return {
        "nombre": "Luis Pérez",
        "correo": "luis@empresa.test",
        "rol": "TECNICO",
        "contrasena": CLAVE,
        **extra,
    }


def reset(api, pk, contrasena=CLAVE_2):
    return api.post(ruta(pk, "restablecer-contrasena"), {"contrasena": contrasena})


OPERACIONES = {
    "crear": lambda a, t: services.crear_usuario(
        a.pk, nombre="Otro", correo="otro@empresa.test", rol=TECNICO, contrasena=CLAVE
    ),
    "editar": lambda a, t: services.editar_usuario(a.pk, t.pk, {"nombre": "Cambiado"}),
    "cambiar_rol": lambda a, t: services.cambiar_rol(a.pk, t.pk, ESPECTADOR),
    "desactivar": lambda a, t: services.desactivar_usuario(a.pk, t.pk),
    "reactivar": lambda a, t: services.reactivar_usuario(a.pk, t.pk),
    "restablecer": lambda a, t: services.restablecer_contrasena(a.pk, t.pk, CLAVE_2),
}
