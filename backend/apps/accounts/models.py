from typing import ClassVar

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models
from django.db.models.functions import Lower
from django.utils.crypto import salted_hmac

from apps.core.constantes import CORREO_MAX, NOMBRE_MAX, NOMINA_MAX

from .managers import UsuarioManager

SOLO_ASCII = r"^[\x01-\x7f]+$"


class Rol(models.TextChoices):
    ADMIN = "ADMIN", "Administrador"
    TECNICO = "TECNICO", "Técnico"
    ESPECTADOR = "ESPECTADOR", "Espectador"


class Usuario(AbstractBaseUser):
    """Usuario del sistema. No hay is_staff ni is_superuser: administrar = rol ADMIN.

    REGLA: toda ruta que escriba `rol` o `activo` debe tomar el candado L0 de administradores
    (`services.usuarios._bloquear`, ver `locks.py`): de eso depende "nunca queda el sistema sin un
    administrador activo". `tests/test_admin_lock_rule.py` lo comprueba con ast.
    """

    Rol = Rol  # alias: `Usuario.Rol` sigue siendo la forma de usarlo

    nombre = models.CharField(max_length=NOMBRE_MAX)
    # NULL cuando no existe (nunca ""); único cuando tiene valor. Obligatoriedad: ver decisiones.md.
    numero_nomina = models.CharField(max_length=NOMINA_MAX, null=True, blank=True)  # noqa: DJ001
    correo = models.EmailField(max_length=CORREO_MAX, unique=True)
    rol = models.CharField(max_length=10, choices=Rol.choices)
    activo = models.BooleanField(default=True)
    intentos_fallidos = models.IntegerField(default=0)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    # Versión de las sesiones del usuario: entra en el hash de sesión. Se incrementa (UPDATE
    # atómico con F) al desactivar y al restablecer la contraseña, bajo los mismos candados, y
    # así las sesiones anteriores quedan inválidas para siempre (aunque se reactive al usuario
    # o se restablezca la MISMA contraseña), sin depender de que se borre ninguna fila.
    # Nunca se expone: ni en respuestas, ni en eventos (sanitize la rechaza), ni en logs.
    sesion_version = models.IntegerField(default=0)

    objects = UsuarioManager()

    USERNAME_FIELD = "correo"
    EMAIL_FIELD = "correo"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["nombre", "rol"]

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(Lower("correo"), name="usuario_correo_unico_ci"),
            models.UniqueConstraint(
                Lower("numero_nomina"),
                condition=models.Q(numero_nomina__isnull=False),
                name="usuario_nomina_unica",
            ),
            models.CheckConstraint(
                condition=models.Q(rol__in=Rol.values),
                name="usuario_rol_valido",
            ),
            models.CheckConstraint(
                condition=models.Q(intentos_fallidos__gte=0),
                name="usuario_intentos_no_negativos",
            ),
            # Decisión de Vul: correo y numero_nomina solo ASCII (los servicios lo validan primero).
            models.CheckConstraint(
                condition=models.Q(correo__regex=SOLO_ASCII), name="usuario_correo_ascii"
            ),
            models.CheckConstraint(
                condition=models.Q(numero_nomina__isnull=True)
                | models.Q(numero_nomina__regex=SOLO_ASCII),
                name="usuario_nomina_ascii",
            ),
        ]

    def __str__(self):
        return self.correo

    @property
    def is_active(self):
        """Nombre que esperan Django y DRF; el dato de negocio es `activo`."""
        return self.activo

    def _hash_de_sesion(self, secret=None):
        sal = "django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash"
        return salted_hmac(
            sal, f"{self.password}:{self.sesion_version}", secret=secret, algorithm="sha256"
        ).hexdigest()

    def get_session_auth_hash(self):
        """HMAC de la contraseña Y de la versión de sesión (ver `sesion_version`). Cambiar esta
        fórmula cierra todas las sesiones existentes una vez, al desplegar."""
        return self._hash_de_sesion()

    def get_session_auth_fallback_hash(self):
        """Lo mismo con las claves de SECRET_KEY_FALLBACKS (Django las prueba al validar)."""
        for secreto in settings.SECRET_KEY_FALLBACKS:
            yield self._hash_de_sesion(secret=secreto)

    def save(self, *args, **kwargs):
        self.correo = self.correo.strip().lower()
        self.numero_nomina = (self.numero_nomina or "").strip() or None
        super().save(*args, **kwargs)
