from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone


class EventoInmutableError(RuntimeError):
    """Los eventos son solo de inserción: no se modifican ni se borran."""


class TipoEvento(models.TextChoices):
    # DEFINICION §6.9, sin los tipos AVISO_*: la bitácora no registra eventos de avisos
    # (decisión de Vul; la tabla Aviso conserva su propio historial).
    INSTRUMENTO_ALTA = "INSTRUMENTO_ALTA"
    INSTRUMENTO_EDITADO = "INSTRUMENTO_EDITADO"
    INSTRUMENTO_CAMBIO_AREA = "INSTRUMENTO_CAMBIO_AREA"
    INSTRUMENTO_CAMBIO_ESTADO = "INSTRUMENTO_CAMBIO_ESTADO"
    CALIBRACION_REGISTRADA = "CALIBRACION_REGISTRADA"
    CERTIFICADO_SUBIDO = "CERTIFICADO_SUBIDO"
    CERTIFICADO_ELIMINADO = "CERTIFICADO_ELIMINADO"
    REEMPLAZO_INICIADO = "REEMPLAZO_INICIADO"
    REEMPLAZO_TERMINADO = "REEMPLAZO_TERMINADO"
    USUARIO_CREADO = "USUARIO_CREADO"
    USUARIO_ROL_CAMBIADO = "USUARIO_ROL_CAMBIADO"
    USUARIO_DESACTIVADO = "USUARIO_DESACTIVADO"
    USUARIO_CONTRASENA_RESTABLECIDA = "USUARIO_CONTRASENA_RESTABLECIDA"
    SESION_INICIADA = "SESION_INICIADA"
    SESION_FALLIDA = "SESION_FALLIDA"
    CATALOGO_MODIFICADO = "CATALOGO_MODIFICADO"
    CONFIGURACION_CAMBIADA = "CONFIGURACION_CAMBIADA"
    PROCESO_DIARIO_EJECUTADO = "PROCESO_DIARIO_EJECUTADO"
    # Agregados fuera de §6.9 (ver docs/decisiones.md)
    CUENTA_BLOQUEADA = "CUENTA_BLOQUEADA"
    USUARIO_CONTRASENA_CAMBIADA = "USUARIO_CONTRASENA_CAMBIADA"
    USUARIO_EDITADO = "USUARIO_EDITADO"
    USUARIO_REACTIVADO = "USUARIO_REACTIVADO"


class EventoQuerySet(models.QuerySet):
    """Bloquea todo lo que modifica o borra filas existentes."""

    def update(self, **kwargs):
        raise EventoInmutableError("Los eventos no se pueden actualizar.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise EventoInmutableError("Los eventos no se pueden actualizar.")

    def delete(self):
        raise EventoInmutableError("Los eventos no se pueden borrar.")

    def bulk_create(self, objs, *args, update_conflicts=False, **kwargs):
        if update_conflicts:
            raise EventoInmutableError("Los eventos no se pueden actualizar.")
        return super().bulk_create(objs, *args, update_conflicts=False, **kwargs)


class Evento(models.Model):
    """Bitácora de auditoría. `usuario` nulo = Sistema. Nunca guarda contraseñas ni hashes."""

    # UTC en la base; se muestra en America/Mexico_City.
    ocurrido_en = models.DateTimeField(default=timezone.now)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Sin FK todavía: se convierte en FK a Instrumento en el sprint 2.
    instrumento_id = models.PositiveBigIntegerField(null=True, blank=True)
    tipo = models.CharField(max_length=40, choices=TipoEvento.choices)
    # Sin fecha: el formato "dd-mm-aaaa hh:mm · ID · texto · usuario" se arma al mostrar.
    descripcion = models.TextField()
    antes = models.JSONField(null=True, blank=True)
    despues = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    objects = EventoQuerySet.as_manager()

    class Meta:
        indexes: ClassVar[list] = [
            # Paginación por cursor de la bitácora: ORDER BY ocurrido_en DESC, id DESC. Su primer
            # campo también cubre las búsquedas por rango de fechas.
            models.Index(fields=["-ocurrido_en", "-id"], name="evento_ocurrido_id_idx"),
            models.Index(fields=["usuario"], name="evento_usuario_idx"),
            models.Index(fields=["tipo"], name="evento_tipo_idx"),
            models.Index(fields=["instrumento_id"], name="evento_instrumento_idx"),
        ]
        constraints: ClassVar[list] = [
            models.CheckConstraint(
                condition=models.Q(tipo__in=TipoEvento.values), name="evento_tipo_valido"
            ),
        ]

    def __str__(self):
        return f"{self.tipo} #{self.pk}"

    def save(self, *args, **kwargs):
        if not self._state.adding or kwargs.get("force_update"):
            raise EventoInmutableError("Los eventos no se pueden modificar.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise EventoInmutableError("Los eventos no se pueden borrar.")
