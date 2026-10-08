from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models
from django.db.models.functions import Lower

from .managers import UsuarioManager


class Usuario(AbstractBaseUser):
    """Usuario del sistema. No hay is_staff ni is_superuser: administrar = rol ADMIN."""

    class Rol(models.TextChoices):
        ADMIN = "ADMIN", "Administrador"
        TECNICO = "TECNICO", "Técnico"
        ESPECTADOR = "ESPECTADOR", "Espectador"

    nombre = models.CharField(max_length=150)
    # Opcional y sin unicidad: DEFINICION §5 no define su obligatoriedad (ver decisiones.md).
    numero_nomina = models.CharField(max_length=20, blank=True, default="")
    correo = models.EmailField(max_length=254, unique=True)
    rol = models.CharField(max_length=10, choices=Rol.choices)
    activo = models.BooleanField(default=True)
    intentos_fallidos = models.IntegerField(default=0)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)
    debe_cambiar_contrasena = models.BooleanField(default=False)
    creado_en = models.DateTimeField(auto_now_add=True)

    objects = UsuarioManager()

    USERNAME_FIELD = "correo"
    EMAIL_FIELD = "correo"
    REQUIRED_FIELDS = ["nombre", "rol"]

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("correo"), name="usuario_correo_unico_ci"),
            models.CheckConstraint(
                condition=models.Q(rol__in=["ADMIN", "TECNICO", "ESPECTADOR"]),
                name="usuario_rol_valido",
            ),
            models.CheckConstraint(
                condition=models.Q(intentos_fallidos__gte=0),
                name="usuario_intentos_no_negativos",
            ),
        ]

    def __str__(self):
        return self.correo

    @property
    def is_active(self):
        """Nombre que esperan Django y DRF; el dato de negocio es `activo`."""
        return self.activo

    def save(self, *args, **kwargs):
        self.correo = self.correo.strip().lower()
        super().save(*args, **kwargs)
