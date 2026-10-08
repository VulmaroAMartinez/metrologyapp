from datetime import UTC, date, datetime

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import Usuario
from apps.accounts.tests.factories import UsuarioFactory
from apps.audit.models import Evento, TipoEvento
from apps.audit.sanitize import CAMPOS_PERMITIDOS, DatosEventoInvalidosError
from apps.audit.services import EventoFueraDeTransaccionError, registrar_evento

pytestmark = pytest.mark.django_db


def registrar(**extra):
    datos = {"tipo": TipoEvento.USUARIO_CREADO, "usuario": None, "descripcion": "prueba", **extra}
    return registrar_evento(**datos)


# --- exige transacción --------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_outside_an_atomic_block_it_raises_and_writes_nothing():
    with pytest.raises(EventoFueraDeTransaccionError):
        registrar()

    assert Evento.objects.count() == 0


def test_inside_an_atomic_block_it_inserts_the_event():
    usuario = UsuarioFactory()

    with transaction.atomic():
        evento = registrar_evento(
            TipoEvento.USUARIO_ROL_CAMBIADO,
            usuario,
            "Rol cambiado",
            antes={"rol": "TECNICO"},
            despues={"rol": "ADMIN"},
            instrumento_id=7,
            ip="198.51.100.4",
        )

    guardado = Evento.objects.get(pk=evento.pk)
    assert (guardado.tipo, guardado.usuario, guardado.descripcion) == (
        "USUARIO_ROL_CAMBIADO",
        usuario,
        "Rol cambiado",
    )
    assert (guardado.antes, guardado.despues) == ({"rol": "TECNICO"}, {"rol": "ADMIN"})
    assert (guardado.instrumento_id, guardado.ip) == (7, "198.51.100.4")


def test_user_none_is_the_system():
    assert registrar().usuario is None


def test_unknown_type_is_rejected():
    with pytest.raises(ValueError, match="desconocido"):
        registrar(tipo="INVENTADO")
    assert Evento.objects.count() == 0


# --- saneamiento --------------------------------------------------------------------------


def test_whitelisted_fields_are_accepted_and_dates_become_text():
    evento = registrar(
        despues={
            "nombre": "Ana",
            "correo": "ana@empresa.test",
            "numero_nomina": None,
            "rol": "ADMIN",
            "activo": True,
            "debe_cambiar_contrasena": False,
            "bloqueado_hasta": datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
            "motivo": "x",
        }
    )

    evento.refresh_from_db()
    assert evento.despues["bloqueado_hasta"] == "2026-10-08T12:00:00+00:00"
    assert registrar(despues={"motivo": date(2026, 10, 8)}).despues == {"motivo": "2026-10-08"}


@pytest.mark.parametrize(
    "campo",
    [
        "password",
        "contrasena",
        "contrasena_actual",
        "contrasena_nueva",
        "password_hash",
        "hash",
        "token",
        "csrfToken",
        "session_key",
        "intentos_fallidos",
        "last_login",
    ],
)
@pytest.mark.parametrize("lado", ["antes", "despues"])
def test_forbidden_fields_are_rejected_and_nothing_is_written(campo, lado):
    with pytest.raises(DatosEventoInvalidosError, match=campo):
        registrar(**{lado: {"rol": "ADMIN", campo: "lo-que-sea"}})

    assert Evento.objects.count() == 0


def test_a_real_password_hash_cannot_be_smuggled_in():
    usuario = UsuarioFactory()

    with pytest.raises(DatosEventoInvalidosError):
        registrar(despues={"password": usuario.password})
    with pytest.raises(DatosEventoInvalidosError, match="hash"):
        registrar(despues={"nombre": "argon2$argon2id$v=19$m=102400,t=2,p=8$abc$def"})
    with pytest.raises(DatosEventoInvalidosError, match="hash"):
        registrar(despues={"motivo": "pbkdf2_sha256$600000$salt$hash"})


@pytest.mark.parametrize("valor", [{"anidado": 1}, ["lista"], object()])
def test_only_scalar_values_are_accepted(valor):
    with pytest.raises(DatosEventoInvalidosError, match="escalar"):
        registrar(despues={"nombre": valor})


@pytest.mark.parametrize("datos", ["texto", ["rol"], 5])
def test_antes_and_despues_must_be_a_dict_or_none(datos):
    with pytest.raises(DatosEventoInvalidosError, match="diccionario"):
        registrar(antes=datos)


def test_the_whitelist_never_includes_secrets_or_counters():
    prohibidos = {"password", "intentos_fallidos", "last_login", "contrasena", "token"}
    assert not (CAMPOS_PERMITIDOS & prohibidos)
    assert not any("pass" in campo or "hash" in campo for campo in CAMPOS_PERMITIDOS)
    # El único campo con "contrasena" es el indicador booleano de cambio obligatorio.
    assert {c for c in CAMPOS_PERMITIDOS if "contrasena" in c} == {"debe_cambiar_contrasena"}


# --- el cambio y su evento se confirman o se revierten juntos --------------------------------


@pytest.mark.django_db(transaction=True)
def test_if_the_change_fails_no_event_remains():
    usuario = UsuarioFactory(rol=Usuario.Rol.TECNICO)
    otro = UsuarioFactory(correo="ocupado@empresa.test")

    with pytest.raises(IntegrityError), transaction.atomic():
        registrar_evento(TipoEvento.USUARIO_ROL_CAMBIADO, usuario, "Rol cambiado")
        usuario.rol = Usuario.Rol.ADMIN
        usuario.correo = otro.correo  # viola la unicidad: el cambio falla después del evento
        usuario.save()

    usuario.refresh_from_db()
    assert Evento.objects.count() == 0
    assert usuario.rol == Usuario.Rol.TECNICO


@pytest.mark.django_db(transaction=True)
def test_if_the_event_fails_the_change_is_reverted():
    usuario = UsuarioFactory(rol=Usuario.Rol.TECNICO)

    with pytest.raises(DatosEventoInvalidosError), transaction.atomic():
        usuario.rol = Usuario.Rol.ADMIN
        usuario.save()
        registrar_evento(
            TipoEvento.USUARIO_ROL_CAMBIADO,
            usuario,
            "Rol cambiado",
            despues={"password": "no-debe-entrar"},
        )

    usuario.refresh_from_db()
    assert usuario.rol == Usuario.Rol.TECNICO
    assert Evento.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_change_and_event_commit_together():
    usuario = UsuarioFactory(rol=Usuario.Rol.TECNICO)

    with transaction.atomic():
        usuario.rol = Usuario.Rol.ADMIN
        usuario.save()
        registrar_evento(TipoEvento.USUARIO_ROL_CAMBIADO, usuario, "Rol cambiado")

    usuario.refresh_from_db()
    assert usuario.rol == Usuario.Rol.ADMIN
    assert Evento.objects.filter(tipo="USUARIO_ROL_CAMBIADO", usuario=usuario).count() == 1
