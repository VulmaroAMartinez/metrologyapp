from rest_framework import serializers

from apps.core.constantes import CORREO_MAX, MAX_PASSWORD_LENGTH, NOMBRE_MAX, NOMINA_MAX

from .models import Usuario


class LoginSerializer(serializers.Serializer):
    correo = serializers.EmailField(max_length=CORREO_MAX)
    contrasena = serializers.CharField(
        max_length=MAX_PASSWORD_LENGTH, trim_whitespace=False, write_only=True
    )


class CambiarContrasenaSerializer(serializers.Serializer):
    contrasena_actual = serializers.CharField(
        max_length=MAX_PASSWORD_LENGTH, trim_whitespace=False, write_only=True
    )
    contrasena_nueva = serializers.CharField(
        max_length=MAX_PASSWORD_LENGTH, trim_whitespace=False, write_only=True
    )


class UsuarioActualSerializer(serializers.ModelSerializer):
    """Lo único que se expone del usuario: nunca el hash ni los contadores de bloqueo."""

    class Meta:
        model = Usuario
        fields = ("id", "nombre", "correo", "rol", "numero_nomina")
        read_only_fields = fields


# --- gestión de usuarios (solo ADMIN) ------------------------------------------------------------

ORDENES = ("nombre", "correo", "rol", "activo", "creado_en")


class UsuarioAdminSerializer(serializers.ModelSerializer):
    """Lo que ve el administrador. Nunca el hash ni intentos_fallidos; bloqueado_hasta es de
    solo lectura (no existe desbloqueo manual)."""

    class Meta:
        model = Usuario
        fields = (
            "id",
            "nombre",
            "correo",
            "numero_nomina",
            "rol",
            "activo",
            "creado_en",
            "bloqueado_hasta",
        )
        read_only_fields = fields


class EstrictoSerializer(serializers.Serializer):
    """Rechaza campos desconocidos en vez de ignorarlos en silencio (p. ej. una `contrasena`
    escrita por el administrador, o un `rol` dentro de una edición)."""

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            sobrantes = sorted(set(data) - set(self.fields))
            if sobrantes:
                raise serializers.ValidationError(
                    {campo: ["Campo no permitido."] for campo in sobrantes}
                )
        return super().to_internal_value(data)


class CrearUsuarioSerializer(EstrictoSerializer):
    nombre = serializers.CharField(max_length=NOMBRE_MAX)
    correo = serializers.EmailField(max_length=CORREO_MAX)
    rol = serializers.ChoiceField(choices=Usuario.Rol.choices)
    # La escribe el administrador; pasa por los validadores y nunca se devuelve ni se registra.
    contrasena = serializers.CharField(
        max_length=MAX_PASSWORD_LENGTH, trim_whitespace=False, write_only=True
    )
    numero_nomina = serializers.CharField(
        max_length=NOMINA_MAX, required=False, allow_null=True, allow_blank=True
    )


class EditarUsuarioSerializer(EstrictoSerializer):
    nombre = serializers.CharField(max_length=NOMBRE_MAX, required=False)
    correo = serializers.EmailField(max_length=CORREO_MAX, required=False)
    numero_nomina = serializers.CharField(
        max_length=NOMINA_MAX, required=False, allow_null=True, allow_blank=True
    )


class RestablecerContrasenaSerializer(EstrictoSerializer):
    contrasena = serializers.CharField(
        max_length=MAX_PASSWORD_LENGTH, trim_whitespace=False, write_only=True
    )


class CambiarRolSerializer(EstrictoSerializer):
    rol = serializers.ChoiceField(choices=Usuario.Rol.choices)


class FiltrosUsuariosSerializer(serializers.Serializer):
    rol = serializers.ChoiceField(choices=Usuario.Rol.choices, required=False)
    activo = serializers.BooleanField(required=False)
    buscar = serializers.CharField(max_length=100, required=False, allow_blank=True)
    orden = serializers.ChoiceField(
        choices=[*ORDENES, *(f"-{campo}" for campo in ORDENES)], required=False
    )
