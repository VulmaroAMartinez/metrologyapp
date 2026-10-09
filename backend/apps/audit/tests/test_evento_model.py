import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.accounts.tests.factories import UsuarioFactory
from apps.audit.models import Evento, EventoInmutableError, TipoEvento

pytestmark = pytest.mark.django_db

# DEFINICION §6.9, copiada tal cual, más los dos tipos agregados (ver docs/decisiones.md).
TIPOS_DEFINICION = {
    "INSTRUMENTO_ALTA",
    "INSTRUMENTO_EDITADO",
    "INSTRUMENTO_CAMBIO_AREA",
    "INSTRUMENTO_CAMBIO_ESTADO",
    "CALIBRACION_REGISTRADA",
    "CERTIFICADO_SUBIDO",
    "CERTIFICADO_ELIMINADO",
    "REEMPLAZO_INICIADO",
    "REEMPLAZO_TERMINADO",
    "USUARIO_CREADO",
    "USUARIO_ROL_CAMBIADO",
    "USUARIO_DESACTIVADO",
    "USUARIO_CONTRASENA_RESTABLECIDA",
    "SESION_INICIADA",
    "SESION_FALLIDA",
    "CATALOGO_MODIFICADO",
    "CONFIGURACION_CAMBIADA",
    "PROCESO_DIARIO_EJECUTADO",
}
TIPOS_AGREGADOS = {
    "CUENTA_BLOQUEADA",
    "USUARIO_CONTRASENA_CAMBIADA",
    "USUARIO_EDITADO",
    "USUARIO_REACTIVADO",
}


def crear(**extra):
    datos = {"tipo": TipoEvento.SESION_INICIADA, "descripcion": "prueba", **extra}
    return Evento.objects.create(**datos)


def test_event_types_are_exactly_the_definition_plus_the_added_ones():
    assert set(TipoEvento.values) == TIPOS_DEFINICION | TIPOS_AGREGADOS


def test_str_shows_type_and_id():
    evento = crear()

    assert str(evento) == f"SESION_INICIADA #{evento.pk}"


TIPOS_AVISO = ["AVISO_CREADO", "AVISO_EDITADO", "AVISO_CANCELADO", "AVISO_ENVIADO", "AVISO_FALLIDO"]


@pytest.mark.parametrize("tipo", TIPOS_AVISO)
def test_notice_event_types_do_not_exist_in_code_or_database(tipo):
    assert tipo not in TipoEvento.values
    assert not hasattr(TipoEvento, tipo)
    with pytest.raises(IntegrityError, match="evento_tipo_valido"), transaction.atomic():
        Evento.objects.bulk_create([Evento(tipo=tipo, descripcion="x")])


def test_the_daily_process_event_type_is_kept():
    assert "PROCESO_DIARIO_EJECUTADO" in TipoEvento.values


def test_insert_with_defaults_means_system_event():
    antes = timezone.now()

    evento = crear()
    evento.refresh_from_db()

    assert evento.usuario is None  # Sistema
    assert (evento.instrumento_id, evento.antes, evento.despues, evento.ip) == (None,) * 4
    assert timezone.is_aware(evento.ocurrido_en) and antes <= evento.ocurrido_en <= timezone.now()


def test_all_fields_round_trip():
    usuario = UsuarioFactory()

    evento = crear(
        usuario=usuario,
        instrumento_id=42,
        antes={"rol": "TECNICO"},
        despues={"rol": "ADMIN"},
        ip="203.0.113.7",
    )
    evento.refresh_from_db()

    assert (evento.usuario, evento.instrumento_id, evento.ip) == (usuario, 42, "203.0.113.7")
    assert (evento.antes, evento.despues) == ({"rol": "TECNICO"}, {"rol": "ADMIN"})


# --- solo inserción en el código ----------------------------------------------------------


def test_saving_an_existing_event_is_rejected():
    evento = crear()
    evento.descripcion = "alterada"

    with pytest.raises(EventoInmutableError):
        evento.save()
    with pytest.raises(EventoInmutableError):
        evento.save(update_fields=["descripcion"])
    assert Evento.objects.get(pk=evento.pk).descripcion == "prueba"


def test_force_update_on_a_new_instance_is_rejected():
    with pytest.raises(EventoInmutableError):
        Evento(tipo=TipoEvento.SESION_INICIADA, descripcion="x").save(force_update=True)


def test_deleting_an_event_is_rejected():
    evento = crear()

    with pytest.raises(EventoInmutableError):
        evento.delete()
    assert Evento.objects.filter(pk=evento.pk).exists()


def test_queryset_update_delete_and_bulk_update_are_rejected():
    evento = crear()

    with pytest.raises(EventoInmutableError):
        Evento.objects.all().update(descripcion="alterada")
    with pytest.raises(EventoInmutableError):
        Evento.objects.filter(pk=evento.pk).delete()
    with pytest.raises(EventoInmutableError):
        Evento.objects.all().delete()
    with pytest.raises(EventoInmutableError):
        Evento.objects.bulk_update([evento], ["descripcion"])
    assert Evento.objects.get(pk=evento.pk).descripcion == "prueba"


def test_bulk_create_inserts_but_cannot_upsert():
    Evento.objects.bulk_create([Evento(tipo=TipoEvento.SESION_FALLIDA, descripcion="a")])
    assert Evento.objects.count() == 1

    with pytest.raises(EventoInmutableError):
        Evento.objects.bulk_create(
            [Evento(pk=1, tipo=TipoEvento.SESION_FALLIDA, descripcion="b")],
            update_conflicts=True,
            update_fields=["descripcion"],
            unique_fields=["id"],
        )


def test_the_block_also_applies_through_the_user_relation_queryset():
    usuario = UsuarioFactory()
    crear(usuario=usuario)

    with pytest.raises(EventoInmutableError):
        Evento.objects.filter(usuario=usuario).update(descripcion="alterada")


# --- restricciones, índices y usuario protegido -------------------------------------------


def test_unknown_event_type_is_rejected_by_the_database():
    with pytest.raises(IntegrityError, match="evento_tipo_valido"), transaction.atomic():
        Evento.objects.bulk_create([Evento(tipo="INVENTADO", descripcion="x")])


def test_a_user_with_events_cannot_be_deleted():
    usuario = UsuarioFactory()
    crear(usuario=usuario)

    with pytest.raises(ProtectedError):
        usuario.delete()


def test_indexes_and_constraint_exist_in_the_database():
    with connection.cursor() as cursor:
        restricciones = connection.introspection.get_constraints(cursor, "audit_evento")

    indices = {tuple(c["columns"]) for c in restricciones.values() if c["index"]}
    for columnas in [("ocurrido_en",), ("usuario_id",), ("tipo",), ("instrumento_id",)]:
        assert columnas in indices, columnas
    assert restricciones["evento_tipo_valido"]["check"] is True
    nombres = set(restricciones)
    assert {"evento_ocurrido_en_idx", "evento_tipo_idx", "evento_instrumento_idx"} <= nombres


def test_the_migration_refuses_to_run_if_notice_events_exist():
    """Los eventos no se pueden borrar: con filas AVISO_* la migración avisa y se detiene."""
    import importlib
    from types import SimpleNamespace

    migracion = importlib.import_module("apps.audit.migrations.0003_quitar_tipos_aviso")

    def apps_con(cantidad):
        modelo = SimpleNamespace(
            objects=SimpleNamespace(filter=lambda **kwargs: SimpleNamespace(count=lambda: cantidad))
        )
        return SimpleNamespace(get_model=lambda app, nombre: modelo)

    migracion.verificar_sin_eventos_de_aviso(apps_con(0), None)  # sin filas: continúa
    with pytest.raises(RuntimeError, match="3 eventos con tipo AVISO_"):
        migracion.verificar_sin_eventos_de_aviso(apps_con(3), None)
