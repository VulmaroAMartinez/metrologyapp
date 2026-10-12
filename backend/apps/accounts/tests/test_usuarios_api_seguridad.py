"""API de usuarios, seguridad: 401/403 en todo, ninguna respuesta con hashes ni contraseñas."""

import json
import logging
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Usuario
from apps.audit.models import Evento
from apps.core.testing import ME, Api, UsuarioFactory

from .ayudantes import (
    BASE,
    CLAVE,
    CLAVE_2,
    ESPECTADOR,
    TECNICO,
    evento_que_falla,
    nuevo,
    reset,
    ruta,
    sesion,
    valores,
)

pytestmark = pytest.mark.django_db


# --- técnico y espectador: 403 en todo ---


def todos_los_endpoints(pk):
    return [
        ("GET", BASE, None),
        ("POST", BASE, nuevo(correo="x@empresa.test")),
        ("GET", ruta(pk), None),
        ("PATCH", ruta(pk), {"nombre": "Hackeado"}),
        ("POST", ruta(pk, "cambiar-rol"), {"rol": "ADMIN"}),
        ("POST", ruta(pk, "desactivar"), {}),
        ("POST", ruta(pk, "reactivar"), {}),
        ("POST", ruta(pk, "restablecer-contrasena"), {"contrasena": CLAVE_2}),
    ]


@pytest.mark.parametrize("rol", [TECNICO, ESPECTADOR])
def test_technicians_and_spectators_get_403_on_every_endpoint(rol, admin):
    objetivo = UsuarioFactory(correo="victima@empresa.test", rol=TECNICO)
    api = sesion(UsuarioFactory(correo="intruso@empresa.test", rol=rol))
    antes = (Usuario.objects.count(), Evento.objects.count())
    instantanea = list(Usuario.objects.order_by("pk").values())

    for metodo, destino, cuerpo in todos_los_endpoints(objetivo.pk):
        respuesta = api.call(metodo, destino, cuerpo)

        assert respuesta.status_code == 403, (metodo, destino)
        assert respuesta.json()["codigo"] == "permiso_denegado"
    assert (Usuario.objects.count(), Evento.objects.count()) == antes
    assert list(Usuario.objects.order_by("pk").values()) == instantanea


def test_anonymous_gets_401_on_every_endpoint(admin):
    api = Api()
    api.csrf()

    for metodo, destino, cuerpo in todos_los_endpoints(admin.pk):
        assert api.call(metodo, destino, cuerpo).status_code == 401, (metodo, destino)


# --- ninguna respuesta contiene hashes ---


def test_no_response_ever_contains_a_password_hash(real_hasher, admin):
    """Con el hasher REAL (Argon2): recorre todas las respuestas JSON y falla si cualquier valor
    empieza con «argon2$» (o aparece una columna de hash o de contador)."""
    api = Api()
    assert api.login(admin.correo).status_code == 200
    bloqueada = UsuarioFactory(
        correo="bloqueada@empresa.test",
        intentos_fallidos=5,
        bloqueado_hasta=timezone.now() + timedelta(minutes=5),
    )
    assert bloqueada.password.startswith("argon2$")  # el hash existe y es real
    cuerpos = []

    def llamar(metodo, destino, datos=None):
        respuesta = api.call(metodo, destino, datos)
        cuerpos.append(respuesta.content.decode())
        return respuesta

    creado = llamar("POST", BASE, nuevo()).json()
    llamar("GET", BASE)
    llamar("GET", f"{BASE}?tamano=100&orden=-creado_en&buscar=lu")
    llamar("GET", ruta(creado["id"]))
    llamar("GET", ruta(bloqueada.pk))
    llamar("PATCH", ruta(creado["id"]), {"nombre": "Luis P."})
    llamar("POST", ruta(creado["id"], "cambiar-rol"), {"rol": "ESPECTADOR"})
    llamar("POST", ruta(creado["id"], "desactivar"))
    llamar("POST", ruta(creado["id"], "reactivar"))
    llamar("POST", ruta(bloqueada.pk, "restablecer-contrasena"), {"contrasena": CLAVE_2})
    llamar("POST", BASE, nuevo())  # duplicado -> error
    llamar("GET", ruta(987654))  # 404
    llamar("GET", f"{BASE}?orden=password")  # 400
    llamar("GET", ME)

    assert len(cuerpos) == 14
    for cuerpo in cuerpos:
        assert CLAVE not in cuerpo
        assert CLAVE_2 not in cuerpo
        for valor in valores(json.loads(cuerpo)):
            assert not (isinstance(valor, str) and "argon2$" in valor), cuerpo
            assert valor not in {"password", "intentos_fallidos", "last_login"}, cuerpo


# --- las contraseñas escritas no aparecen en respuestas, eventos ni logs ---


def test_passwords_never_reach_responses_events_or_logs_after_each_operation(api_admin, caplog):
    caplog.set_level(logging.DEBUG)
    objetivo = UsuarioFactory(correo="b@empresa.test")
    secretos = [CLAVE, CLAVE_2, "qwertyuiop", "otra-clave-larga-55"]
    respuestas = []

    def comprobar():
        texto = json.dumps(list(Evento.objects.values()), default=str) + caplog.text
        texto += "".join(respuestas)
        for secreto in secretos:
            assert secreto not in texto, secreto

    respuestas.append(api_admin.post(BASE, nuevo()).content.decode())
    comprobar()
    respuestas.append(reset(api_admin, objetivo.pk).content.decode())
    comprobar()
    # errores: validadores, duplicado, usuario inexistente, campo no permitido
    respuestas.append(
        api_admin.post(BASE, nuevo(correo="c@e.test", contrasena="qwertyuiop")).content.decode()
    )
    respuestas.append(api_admin.post(BASE, nuevo()).content.decode())  # correo duplicado
    respuestas.append(reset(api_admin, 987654, "otra-clave-larga-55").content.decode())
    respuestas.append(reset(api_admin, objetivo.pk, "qwertyuiop").content.decode())
    api_admin.call("PATCH", ruta(objetivo.pk), {"nombre": "Nuevo Nombre"})
    api_admin.post(ruta(objetivo.pk, "cambiar-rol"), {"rol": "ESPECTADOR"})
    api_admin.post(ruta(objetivo.pk, "desactivar"))
    api_admin.post(ruta(objetivo.pk, "reactivar"))
    comprobar()


def test_a_failure_in_the_middle_never_leaks_the_password_to_the_client_or_the_logs(admin, caplog):
    caplog.set_level(logging.DEBUG)
    api = Api(raise_request_exception=False)
    api.client.force_login(admin)
    api.csrf()
    objetivo = UsuarioFactory(correo="b@empresa.test")

    with evento_que_falla():
        crear_fallido = api.post(BASE, nuevo(correo="otro@empresa.test"))
        reset_fallido = reset(api, objetivo.pk)

    assert crear_fallido.status_code == 500
    assert reset_fallido.status_code == 500
    for secreto in (CLAVE, CLAVE_2):
        assert secreto not in crear_fallido.content.decode() + reset_fallido.content.decode()
        assert secreto not in caplog.text
