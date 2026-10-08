"""Dos administradores operando a la vez.

El sistema nunca debe quedar sin un administrador activo. Cada servicio bloquea (FOR NO KEY
UPDATE) las filas de administradores activos dentro de su transacción y revalida al actor; la
espera artificial en el evento ensancha la ventana de la carrera: sin ese candado, ambos
pasarían la comprobación y los dos cambios se confirmarían.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest
from django.db import connection

from apps.accounts import services
from apps.accounts.models import Usuario
from apps.audit.models import Evento

from .conftest import Api
from .factories import UsuarioFactory

pytestmark = pytest.mark.django_db(transaction=True)

ADMIN, TECNICO = Usuario.Rol.ADMIN, Usuario.Rol.TECNICO
BASE = "/api/v1/usuarios/"


def en_paralelo(*tareas):
    """Lanza las tareas a la vez; devuelve ('ok', valor) o ('error', excepción) por tarea."""
    barrera = threading.Barrier(len(tareas))

    def correr(tarea):
        try:
            barrera.wait(timeout=10)
            return "ok", tarea()
        except services.ErrorDeUsuario as error:
            return "error", error
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(tareas)) as pool:
        return list(pool.map(correr, tareas))


@pytest.fixture
def evento_lento():
    real = services.registrar_evento

    def lento(*args, **kwargs):
        time.sleep(0.3)  # mantiene abierta la transacción y sus candados
        return real(*args, **kwargs)

    with mock.patch.object(services, "registrar_evento", side_effect=lento):
        yield


def administradores_activos():
    return Usuario.objects.filter(rol=ADMIN, activo=True).count()


def admins(n):
    return [UsuarioFactory(correo=f"admin{i}@empresa.test", rol=ADMIN) for i in range(n)]


def test_two_admins_cannot_demote_each_other_at_the_same_time(evento_lento):
    ana, beto = admins(2)

    resultados = en_paralelo(
        lambda: services.cambiar_rol(ana.pk, beto.pk, TECNICO),
        lambda: services.cambiar_rol(beto.pk, ana.pk, TECNICO),
    )

    estados = sorted(estado for estado, _ in resultados)
    assert estados == ["error", "ok"], resultados
    fallo = next(valor for estado, valor in resultados if estado == "error")
    assert isinstance(fallo, services.ActorNoVigente) and fallo.estado == 403
    assert administradores_activos() == 1  # nunca 0


def test_two_admins_cannot_deactivate_each_other_at_the_same_time(evento_lento):
    ana, beto = admins(2)

    resultados = en_paralelo(
        lambda: services.desactivar_usuario(ana.pk, beto.pk),
        lambda: services.desactivar_usuario(beto.pk, ana.pk),
    )

    assert sorted(estado for estado, _ in resultados) == ["error", "ok"], resultados
    assert administradores_activos() == 1
    assert Evento.objects.filter(tipo="USUARIO_DESACTIVADO").count() == 1


def test_a_cycle_of_three_admins_always_leaves_at_least_one(evento_lento):
    ana, beto, carla = admins(3)

    resultados = en_paralelo(
        lambda: services.desactivar_usuario(ana.pk, beto.pk),
        lambda: services.desactivar_usuario(beto.pk, carla.pk),
        lambda: services.desactivar_usuario(carla.pk, ana.pk),
    )

    assert administradores_activos() >= 1
    for estado, valor in resultados:
        assert estado == "ok" or isinstance(valor, services.ActorNoVigente), resultados
    desactivados = Evento.objects.filter(tipo="USUARIO_DESACTIVADO").count()
    assert desactivados == 3 - administradores_activos()  # cada baja tiene su evento


def test_demoting_and_resetting_concurrently_keeps_events_and_state_consistent(evento_lento):
    ana, beto = admins(2)

    resultados = en_paralelo(
        lambda: services.cambiar_rol(ana.pk, beto.pk, TECNICO),
        lambda: services.restablecer_contrasena(beto.pk, ana.pk),
    )

    assert administradores_activos() >= 1
    ok = [valor for estado, valor in resultados if estado == "ok"]
    assert 1 <= len(ok) <= 2
    assert Evento.objects.count() == len(ok)  # un evento por operación confirmada


def test_simultaneous_submissions_of_the_same_form_create_one_user_and_one_clean_error():
    ana, beto = admins(2)

    def crear(actor):
        return lambda: services.crear_usuario(
            actor.pk, nombre="Luis", correo="luis@empresa.test", rol=TECNICO
        )

    resultados = en_paralelo(crear(ana), crear(beto))

    assert sorted(estado for estado, _ in resultados) == ["error", "ok"], resultados
    fallo = next(valor for estado, valor in resultados if estado == "error")
    assert isinstance(fallo, services.CorreoDuplicado) and fallo.estado == 400
    assert Usuario.objects.filter(correo="luis@empresa.test").count() == 1


def test_the_unique_index_resolves_the_race_when_the_precheck_is_bypassed():
    ana, beto = admins(2)

    def crear(actor):
        return lambda: services.crear_usuario(
            actor.pk,
            nombre="Luis",
            correo="luis@empresa.test",
            rol=TECNICO,
            numero_nomina="N-1",
        )

    with mock.patch.object(services, "_verificar_duplicados"):  # simula pasar la comprobación
        resultados = en_paralelo(crear(ana), crear(beto))

    assert sorted(estado for estado, _ in resultados) == ["error", "ok"], resultados
    fallo = next(valor for estado, valor in resultados if estado == "error")
    assert isinstance(fallo, (services.CorreoDuplicado, services.NominaDuplicada))
    assert Usuario.objects.filter(correo="luis@empresa.test").count() == 1


def test_two_http_submissions_of_the_same_form_give_201_and_400_never_500(settings):
    settings.LOGIN_RATE_PER_IP_EMAIL = settings.LOGIN_RATE_PER_IP = 1000
    ana, beto = admins(2)
    sesiones = []
    for admin in (ana, beto):
        api = Api()
        assert api.login(admin.correo).status_code == 200
        sesiones.append(api)
    cuerpo = {"nombre": "Luis", "correo": "luis@empresa.test", "rol": "TECNICO"}

    resultados = en_paralelo(*[(lambda a=api: a.post(BASE, cuerpo)) for api in sesiones])

    respuestas = [valor for _, valor in resultados]
    assert sorted(r.status_code for r in respuestas) == [201, 400]
    rechazada = next(r for r in respuestas if r.status_code == 400)
    assert rechazada.json()["codigo"] == "correo_duplicado"
    assert Usuario.objects.filter(correo="luis@empresa.test").count() == 1
