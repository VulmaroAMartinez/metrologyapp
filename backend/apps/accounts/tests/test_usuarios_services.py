"""Servicios de gestión de usuarios: cada uno atómico con su evento, reglas de administradores,
normalización, duplicados, contraseñas escritas por el administrador y sesiones."""

import json
import logging
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import override_settings
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Usuario
from apps.audit.models import Evento

from .conftest import Api
from .factories import DEFAULT_PASSWORD, UsuarioFactory

pytestmark = pytest.mark.django_db

ADMIN, TECNICO, ESPECTADOR = Usuario.Rol.ADMIN, Usuario.Rol.TECNICO, Usuario.Rol.ESPECTADOR
CLAVE = "tornillo-azul-77-nube"
CLAVE_2 = "nube-de-algodon-88-azul"


@pytest.fixture
def ana(db):
    return UsuarioFactory(correo="ana@empresa.test", nombre="Ana", rol=ADMIN)


@pytest.fixture
def beto(db):
    return UsuarioFactory(correo="beto@empresa.test", nombre="Beto", rol=ADMIN)


@pytest.fixture
def carla(db):
    return UsuarioFactory(correo="carla@empresa.test", nombre="Carla", rol=TECNICO)


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


# --- no quedan restos del modelo anterior ---


def test_there_is_no_forced_change_and_no_temporary_password_left():
    campos = {campo.name for campo in Usuario._meta.get_fields()}

    assert "debe_cambiar_contrasena" not in campos
    assert not hasattr(services, "generar_contrasena_temporal")
    assert not hasattr(services, "PasswordChangeNotRequired")


# --- crear: normalización y validación ---


def test_create_normalizes_fields_and_stores_the_password_the_admin_wrote(ana):
    usuario = crear(
        ana, nombre="  Luis  Pérez ", correo="  Luis.Perez@Empresa.TEST ", numero_nomina=" A-77 "
    )

    usuario.refresh_from_db()
    assert (usuario.nombre, usuario.correo, usuario.numero_nomina) == (
        "Luis  Pérez",
        "luis.perez@empresa.test",
        "A-77",
    )
    assert usuario.activo is True
    assert usuario.check_password(CLAVE)
    assert (usuario.intentos_fallidos, usuario.bloqueado_hasta) == (0, None)


@pytest.mark.parametrize("vacio", ["", "   ", None])
def test_blank_nomina_becomes_null(ana, vacio):
    usuario = crear(ana, numero_nomina=vacio)

    usuario.refresh_from_db()
    assert usuario.numero_nomina is None


@pytest.mark.parametrize(
    ("campos", "fragmento"),
    [
        ({"nombre": ""}, "nombre"),
        ({"nombre": "   "}, "nombre"),
        ({"nombre": "x" * 151}, "150"),
        ({"correo": "no-es-correo"}, "correo"),
        ({"correo": ""}, "correo"),
        ({"correo": "a@" + "b" * 250 + ".com"}, "correo"),
        ({"rol": "SUPERADMIN"}, "rol"),
        ({"numero_nomina": "9" * 21}, "20"),
    ],
)
def test_create_rejects_invalid_data(ana, campos, fragmento):
    with pytest.raises(services.DatosDeUsuarioInvalidos) as error:
        crear(ana, **campos)

    assert fragmento in error.value.detalle.lower()
    assert Usuario.objects.count() == 1
    assert Evento.objects.count() == 0


def test_name_at_the_maximum_length_is_accepted(ana):
    assert len(crear(ana, nombre="x" * 150).nombre) == 150


# --- la contraseña la escribe el administrador y pasa por los validadores ---


@pytest.mark.parametrize(
    ("contrasena", "fragmento"),
    [
        ("Ab1-cdef9", "10 caracteres"),
        ("qwertyuiop", "muy común"),
        ("48291746503", "numérica"),
        ("nuevo@empresa.test1", "muy similar"),
    ],
)
def test_create_runs_the_password_validators_without_echoing_it(ana, contrasena, fragmento):
    with pytest.raises(services.DatosDeUsuarioInvalidos) as error:
        crear(ana, contrasena=contrasena)

    assert fragmento in error.value.detalle
    assert contrasena not in error.value.detalle
    assert contrasena not in str(error.value)
    assert Usuario.objects.count() == 1
    assert Evento.objects.count() == 0


@pytest.mark.parametrize(
    ("contrasena", "fragmento"),
    [("Ab1-cdef9", "10 caracteres"), ("qwertyuiop", "muy común"), ("48291746503", "numérica")],
)
def test_reset_runs_the_password_validators_without_echoing_it(ana, carla, contrasena, fragmento):
    antes = instantanea()

    with pytest.raises(services.DatosDeUsuarioInvalidos) as error:
        services.restablecer_contrasena(ana.pk, carla.pk, contrasena)

    assert fragmento in error.value.detalle
    assert contrasena not in error.value.detalle
    assert instantanea() == antes


def test_reset_rejects_a_password_similar_to_the_targets_own_data(ana, carla):
    with pytest.raises(services.DatosDeUsuarioInvalidos, match="muy similar"):
        services.restablecer_contrasena(ana.pk, carla.pk, "carla@empresa.test1")


def test_system_created_user_validates_the_given_password_without_echoing_it():
    with pytest.raises(services.DatosDeUsuarioInvalidos) as error:
        services.crear_usuario(
            None, nombre="Raíz", correo="raiz@empresa.test", rol=ADMIN, contrasena="qwertyuiop"
        )

    assert "qwertyuiop" not in error.value.detalle


def test_system_created_admin_has_no_actor():
    usuario = services.crear_usuario(
        None, nombre="Raíz", correo="raiz@empresa.test", rol=ADMIN, contrasena=CLAVE
    )

    assert usuario.check_password(CLAVE)
    evento = Evento.objects.get()
    assert (evento.tipo, evento.usuario) == ("USUARIO_CREADO", None)


# --- duplicados ---


@pytest.mark.parametrize(
    "variante", ["ana@empresa.test", "ANA@Empresa.TEST", "  ana@empresa.test  "]
)
def test_duplicate_email_ignores_case_and_spaces(ana, variante):
    with pytest.raises(services.CorreoDuplicado) as error:
        crear(ana, correo=variante)

    assert error.value.codigo == "correo_duplicado"
    assert error.value.estado == 400


@pytest.mark.parametrize("variante", ["ab-12", "AB-12", "  ab-12 ", "Ab-12"])
def test_duplicate_nomina_ignores_case_and_spaces(ana, variante):
    crear(ana, correo="uno@empresa.test", numero_nomina="AB-12")

    with pytest.raises(services.NominaDuplicada) as error:
        crear(ana, correo="dos@empresa.test", numero_nomina=variante)

    assert error.value.codigo == "numero_nomina_duplicado"
    assert error.value.estado == 400


def test_the_unique_index_is_the_last_line_of_defense_for_simultaneous_submissions(ana):
    # Sin la comprobación previa (lo que pasaría con dos envíos simultáneos), el índice único
    # lanza IntegrityError y se traduce al mismo error limpio: nunca un 500.
    crear(ana, correo="uno@empresa.test", numero_nomina="N-1")

    with mock.patch.object(services, "_verificar_duplicados"):
        with pytest.raises(services.CorreoDuplicado):
            crear(ana, correo="UNO@empresa.test")
        with pytest.raises(services.NominaDuplicada):
            crear(ana, correo="otro@empresa.test", numero_nomina="n-1")


def test_unrecognized_integrity_errors_are_not_disguised(ana):
    with (
        mock.patch.object(Usuario.objects, "create_user", side_effect=IntegrityError("otra")),
        pytest.raises(IntegrityError, match="otra"),
    ):
        crear(ana)


def test_edit_cannot_take_another_users_email_or_nomina(ana, beto):
    beto.numero_nomina = "B-1"
    beto.save()

    with pytest.raises(services.CorreoDuplicado):
        services.editar_usuario(ana.pk, ana.pk, {"correo": " BETO@empresa.test"})
    with pytest.raises(services.NominaDuplicada):
        services.editar_usuario(ana.pk, ana.pk, {"numero_nomina": "b-1"})


def test_edit_can_keep_own_email_in_another_case(ana):
    resultado = services.editar_usuario(ana.pk, ana.pk, {"correo": "ANA@empresa.test"})

    assert resultado.correo == "ana@empresa.test"
    assert Evento.objects.count() == 0  # no hubo cambio real


# --- eventos y restricciones por servicio ---


def test_every_service_writes_its_event_with_actor_and_ip(ana, carla):
    usuario = services.crear_usuario(
        ana.pk,
        nombre="Ex",
        correo="ex@empresa.test",
        rol=ESPECTADOR,
        contrasena=CLAVE,
        ip="203.0.113.4",
    )
    services.editar_usuario(ana.pk, usuario.pk, {"nombre": "Ex Dos"}, ip="203.0.113.4")
    services.cambiar_rol(ana.pk, usuario.pk, TECNICO, ip="203.0.113.4")
    services.desactivar_usuario(ana.pk, usuario.pk, ip="203.0.113.4")
    services.reactivar_usuario(ana.pk, usuario.pk, ip="203.0.113.4")
    services.restablecer_contrasena(ana.pk, usuario.pk, CLAVE_2, ip="203.0.113.4")

    eventos = list(Evento.objects.order_by("id"))
    assert [e.tipo for e in eventos] == [
        "USUARIO_CREADO",
        "USUARIO_EDITADO",
        "USUARIO_ROL_CAMBIADO",
        "USUARIO_DESACTIVADO",
        "USUARIO_REACTIVADO",
        "USUARIO_CONTRASENA_RESTABLECIDA",
    ]
    assert {e.usuario_id for e in eventos} == {ana.pk}
    assert {e.ip for e in eventos} == {"203.0.113.4"}
    assert all("ex@empresa.test" in e.descripcion for e in eventos)  # identifica al afectado


def test_event_payloads_carry_before_and_after(ana, carla):
    services.editar_usuario(ana.pk, carla.pk, {"nombre": "Carla Ruiz", "numero_nomina": "C-9"})
    services.cambiar_rol(ana.pk, carla.pk, ESPECTADOR)
    services.desactivar_usuario(ana.pk, carla.pk)

    editado, rol, desactivado = Evento.objects.order_by("id")
    assert editado.antes == {"nombre": "Carla", "numero_nomina": None}
    assert editado.despues == {"nombre": "Carla Ruiz", "numero_nomina": "C-9"}
    assert (rol.antes, rol.despues) == ({"rol": "TECNICO"}, {"rol": "ESPECTADOR"})
    assert (desactivado.antes, desactivado.despues) == ({"activo": True}, {"activo": False})


def test_the_reset_event_names_actor_and_target_and_carries_no_payload(ana, carla):
    services.restablecer_contrasena(ana.pk, carla.pk, CLAVE_2, ip="198.51.100.9")

    evento = Evento.objects.get()
    assert evento.tipo == "USUARIO_CONTRASENA_RESTABLECIDA"
    assert evento.usuario == ana  # quien actúa
    assert "carla@empresa.test" in evento.descripcion  # el afectado
    assert (evento.antes, evento.despues) == (None, None)
    assert CLAVE_2 not in volcado_eventos()


def test_noop_operations_write_nothing(ana, carla):
    services.editar_usuario(ana.pk, carla.pk, {"nombre": "Carla"})
    services.cambiar_rol(ana.pk, carla.pk, TECNICO)
    services.reactivar_usuario(ana.pk, carla.pk)  # ya está activa
    carla.activo = False
    carla.save()
    services.desactivar_usuario(ana.pk, carla.pk)  # ya está inactiva

    assert Evento.objects.count() == 0


# --- atomicidad: el cambio y su evento, juntos, en ambos sentidos ---

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


@pytest.fixture
def objetivo_para(carla):
    def preparar(nombre):
        if nombre == "reactivar":
            carla.activo = False
            carla.save()
        return carla

    return preparar


@pytest.mark.parametrize("nombre", sorted(OPERACIONES))
def test_if_the_event_fails_the_change_is_reverted(nombre, ana, objetivo_para):
    objetivo = objetivo_para(nombre)
    antes = instantanea()

    with (
        mock.patch.object(services, "registrar_evento", side_effect=RuntimeError("falla")),
        pytest.raises(RuntimeError, match="falla"),
    ):
        OPERACIONES[nombre](ana, objetivo)

    assert instantanea() == antes


@pytest.mark.parametrize("nombre", sorted(OPERACIONES))
def test_if_the_change_fails_after_the_event_no_event_remains(nombre, ana, objetivo_para):
    objetivo = objetivo_para(nombre)
    antes = instantanea()
    real = services.registrar_evento

    def evento_y_luego_falla(*args, **kwargs):
        real(*args, **kwargs)  # el evento queda insertado...
        raise RuntimeError("el cambio falla después")  # ...y luego algo falla

    with (
        mock.patch.object(services, "registrar_evento", side_effect=evento_y_luego_falla),
        pytest.raises(RuntimeError, match="después"),
    ):
        OPERACIONES[nombre](ana, objetivo)

    assert instantanea() == antes  # ni el cambio ni el evento


@pytest.mark.parametrize("nombre", ["desactivar", "restablecer"])
def test_if_closing_sessions_fails_nothing_is_changed(nombre, ana, carla):
    antes = instantanea()

    with (
        mock.patch.object(services, "invalidar_sesiones", side_effect=RuntimeError("falla")),
        pytest.raises(RuntimeError),
    ):
        OPERACIONES[nombre](ana, carla)

    assert instantanea() == antes


# --- reglas de administradores ---


def test_an_admin_cannot_deactivate_change_role_or_reset_their_own_password(ana, beto):
    clave_antes = ana.password
    for operacion in (
        lambda: services.desactivar_usuario(ana.pk, ana.pk),
        lambda: services.cambiar_rol(ana.pk, ana.pk, TECNICO),
        lambda: services.restablecer_contrasena(ana.pk, ana.pk, CLAVE_2),
    ):
        with pytest.raises(services.AutoModificacionNoPermitida) as error:
            operacion()
        assert error.value.codigo == "auto_modificacion_no_permitida"
        assert error.value.estado == 403

    ana.refresh_from_db()
    assert ana.activo is True
    assert ana.rol == ADMIN
    assert ana.password == clave_antes
    assert Evento.objects.count() == 0


def test_an_admin_can_edit_their_own_data(ana):
    services.editar_usuario(ana.pk, ana.pk, {"nombre": "Ana María"})

    ana.refresh_from_db()
    assert ana.nombre == "Ana María"


def test_an_admin_can_reset_the_other_admins_password(ana, beto):
    sesion_de(beto)

    services.restablecer_contrasena(ana.pk, beto.pk, CLAVE_2)

    beto.refresh_from_db()
    assert beto.check_password(CLAVE_2)
    assert not beto.check_password(DEFAULT_PASSWORD)
    assert not Session.objects.exists()  # sus sesiones se cerraron


def test_an_admin_can_demote_and_deactivate_another_admin(ana, beto):
    services.cambiar_rol(ana.pk, beto.pk, TECNICO)
    beto.rol = ADMIN
    beto.save()
    services.desactivar_usuario(ana.pk, beto.pk)

    beto.refresh_from_db()
    assert beto.activo is False


def test_the_last_active_admin_invariant(ana, beto):
    with pytest.raises(services.UltimoAdministrador) as error:
        services._garantizar_administrador_restante([ana], ana.pk)
    assert error.value.codigo == "ultimo_administrador"
    assert error.value.estado == 409

    services._garantizar_administrador_restante([ana, beto], ana.pk)  # queda beto


@pytest.mark.parametrize("operacion", sorted(OPERACIONES))
@pytest.mark.parametrize("motivo", ["tecnico", "inactivo", "inexistente"])
def test_an_actor_who_is_no_longer_an_active_admin_is_refused(operacion, motivo, ana, carla):
    actor = UsuarioFactory(correo="exadmin@empresa.test", rol=ADMIN)
    if motivo == "tecnico":
        Usuario.objects.filter(pk=actor.pk).update(rol=TECNICO)
    elif motivo == "inactivo":
        Usuario.objects.filter(pk=actor.pk).update(activo=False)
    else:
        actor.pk = 987654
    antes = instantanea()

    with pytest.raises(services.ActorNoVigente) as error:
        OPERACIONES[operacion](actor, carla)

    assert error.value.estado == 403
    assert error.value.codigo == "permiso_denegado"
    assert instantanea() == antes


@pytest.mark.parametrize(
    "operacion", ["editar", "cambiar_rol", "desactivar", "reactivar", "restablecer"]
)
def test_unknown_target_is_404(operacion, ana):
    objetivo = Usuario(pk=987654)

    with pytest.raises(services.UsuarioInexistente) as error:
        OPERACIONES[operacion](ana, objetivo)

    assert error.value.estado == 404


# --- el restablecimiento por el Sistema (comando de servidor) ---


def test_the_system_can_reset_an_admin_without_an_actor(ana):
    services.restablecer_contrasena(None, ana.pk, CLAVE_2, solo_rol=ADMIN)

    ana.refresh_from_db()
    assert ana.check_password(CLAVE_2)
    evento = Evento.objects.get()
    assert evento.tipo == "USUARIO_CONTRASENA_RESTABLECIDA"
    assert evento.usuario is None
    assert "por comando de servidor" in evento.descripcion
    assert "ana@empresa.test" in evento.descripcion


def test_the_system_reset_refuses_non_admins(carla):
    antes = instantanea()

    with pytest.raises(services.DatosDeUsuarioInvalidos, match="no es administrador"):
        services.restablecer_contrasena(None, carla.pk, CLAVE_2, solo_rol=ADMIN)

    assert instantanea() == antes


def test_the_system_reset_works_even_for_the_only_admin_and_does_not_reactivate(ana):
    Usuario.objects.filter(pk=ana.pk).update(activo=False)

    services.restablecer_contrasena(None, ana.pk, CLAVE_2, solo_rol=ADMIN)

    ana.refresh_from_db()
    assert ana.check_password(CLAVE_2)
    assert ana.activo is False


# --- restablecer / reactivar / desactivar no son un desbloqueo ---


def test_resetting_the_password_does_not_touch_the_lock_counters(ana):
    bloqueo = timezone.now() + timedelta(minutes=10)
    bloqueada = UsuarioFactory(
        correo="bloqueada@empresa.test", intentos_fallidos=5, bloqueado_hasta=bloqueo
    )

    services.restablecer_contrasena(ana.pk, bloqueada.pk, CLAVE_2)

    bloqueada.refresh_from_db()
    assert bloqueada.intentos_fallidos == 5  # sigue igual
    assert bloqueada.bloqueado_hasta == bloqueo  # sigue bloqueada: no es un desbloqueo manual
    assert bloqueada.check_password(CLAVE_2)


def test_the_system_reset_does_not_touch_the_lock_counters_either(ana):
    bloqueo = timezone.now() + timedelta(minutes=10)
    Usuario.objects.filter(pk=ana.pk).update(intentos_fallidos=5, bloqueado_hasta=bloqueo)

    services.restablecer_contrasena(None, ana.pk, CLAVE_2, solo_rol=ADMIN)

    ana.refresh_from_db()
    assert (ana.intentos_fallidos, ana.bloqueado_hasta) == (5, bloqueo)


def test_deactivating_and_reactivating_do_not_touch_the_lock_counters(ana):
    bloqueo = timezone.now() + timedelta(minutes=10)
    usuario = UsuarioFactory(correo="b@empresa.test", intentos_fallidos=5, bloqueado_hasta=bloqueo)

    services.desactivar_usuario(ana.pk, usuario.pk)
    services.reactivar_usuario(ana.pk, usuario.pk)

    usuario.refresh_from_db()
    assert (usuario.intentos_fallidos, usuario.bloqueado_hasta) == (5, bloqueo)


def test_there_is_no_manual_unlock_in_the_services():
    nombres = {n.lower() for n in dir(services)}

    assert not [n for n in nombres if "desbloque" in n or "unlock" in n]


# --- sesiones ---


def sesion_de(usuario):
    api = Api()
    assert api.login(usuario.correo).status_code == 200
    return api


def test_invalidating_sessions_deletes_only_that_users_rows(ana, carla):
    uno, dos, de_ana = sesion_de(carla), sesion_de(carla), sesion_de(ana)
    assert Session.objects.count() == 3

    borradas = services.invalidar_sesiones(carla.pk)

    assert borradas == 2
    assert Session.objects.filter(session_key=de_ana.session_cookie).exists()
    assert not Session.objects.filter(session_key__in=[uno.session_cookie, dos.session_cookie])


def test_invalidating_ignores_anonymous_and_expired_sessions(carla, clock):
    sesion_de(carla)
    clock.advance(hours=2)  # la sesión de Carla ya venció

    assert services.invalidar_sesiones(carla.pk) == 0


def test_deactivate_and_reset_close_the_targets_sessions_in_the_same_transaction(ana, carla):
    sesion_de(carla)
    services.desactivar_usuario(ana.pk, carla.pk)
    assert not Session.objects.exists()

    carla.activo = True
    carla.save()
    sesion_de(carla)
    services.restablecer_contrasena(ana.pk, carla.pk, CLAVE_2)
    assert not Session.objects.exists()


def test_a_role_change_keeps_the_session_but_not_the_old_permissions(ana, carla):
    sesion_de(carla)

    services.cambiar_rol(ana.pk, carla.pk, ESPECTADOR)

    assert (
        Session.objects.count() == 1
    )  # la sesión sigue; el rol se lee de la base en cada petición


# --- nada sensible en eventos, logs, errores ni trazas ---


def test_no_password_or_hash_reaches_events_or_logs(ana, carla, caplog):
    caplog.set_level(logging.DEBUG)

    usuario = crear(ana)
    services.restablecer_contrasena(ana.pk, carla.pk, CLAVE_2)
    services.editar_usuario(ana.pk, usuario.pk, {"nombre": "Otro Nombre"})
    services.cambiar_rol(ana.pk, usuario.pk, ESPECTADOR)
    services.desactivar_usuario(ana.pk, usuario.pk)
    services.reactivar_usuario(ana.pk, usuario.pk)
    usuario.refresh_from_db()
    carla.refresh_from_db()

    texto = volcado_eventos() + caplog.text
    for secreto in (CLAVE, CLAVE_2, usuario.password, carla.password, DEFAULT_PASSWORD):
        assert secreto not in texto
    assert "argon2" not in texto
    assert "md5$" not in texto


@pytest.mark.parametrize(
    "operacion",
    [
        lambda ana, carla: services.crear_usuario(
            ana.pk, nombre="Otro", correo="otro@empresa.test", rol=TECNICO, contrasena=CLAVE
        ),
        lambda ana, carla: services.restablecer_contrasena(ana.pk, carla.pk, CLAVE),
    ],
    ids=["crear", "restablecer"],
)
def test_a_failure_in_the_middle_never_leaks_the_password_through_the_error_or_logs(
    operacion, ana, carla, caplog
):
    caplog.set_level(logging.DEBUG)

    with (
        mock.patch.object(services, "registrar_evento", side_effect=RuntimeError("falla")),
        pytest.raises(RuntimeError) as error,
    ):
        operacion(ana, carla)

    assert CLAVE not in str(error.value)
    assert CLAVE not in caplog.text


def test_validator_failures_never_leak_the_password_through_logs(ana, carla, caplog):
    caplog.set_level(logging.DEBUG)

    with pytest.raises(services.DatosDeUsuarioInvalidos):
        services.restablecer_contrasena(ana.pk, carla.pk, "carla@empresa.test1")

    assert "carla@empresa.test1" not in caplog.text


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.Argon2PasswordHasher"])
def test_a_created_user_authenticates_with_the_real_hasher(ana):
    usuario = crear(ana)

    assert usuario.password.startswith("argon2$argon2id$")
    assert usuario.check_password(CLAVE)


# --- ramas restantes ---


def test_a_well_formed_but_overlong_email_is_rejected(ana):
    largo = "a" * 64 + "@" + ".".join(["b" * 60] * 4) + ".com"

    with pytest.raises(services.DatosDeUsuarioInvalidos, match="largo"):
        crear(ana, correo=largo)


def test_edit_resolves_a_unique_index_clash_when_the_precheck_is_bypassed(ana, beto):
    with (
        mock.patch.object(services, "_verificar_duplicados"),
        pytest.raises(services.CorreoDuplicado),
    ):
        services.editar_usuario(ana.pk, beto.pk, {"correo": "ANA@empresa.test"})

    beto.refresh_from_db()
    assert beto.correo == "beto@empresa.test"
    assert Evento.objects.count() == 0


def test_change_role_rejects_an_unknown_role(ana, carla):
    with pytest.raises(services.DatosDeUsuarioInvalidos):
        services.cambiar_rol(ana.pk, carla.pk, "SUPERADMIN")


def test_a_validator_rejection_message_is_built_from_the_validators_text(ana, carla):
    with (
        mock.patch.object(
            services, "validate_password", side_effect=ValidationError(["Regla incumplida."])
        ),
        pytest.raises(services.DatosDeUsuarioInvalidos, match="Regla incumplida"),
    ):
        services.restablecer_contrasena(ana.pk, carla.pk, CLAVE)
