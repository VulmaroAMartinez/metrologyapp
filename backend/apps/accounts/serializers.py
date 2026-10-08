from rest_framework import serializers

from .models import Usuario

MAX_PASSWORD_LENGTH = 256  # evita enviar contraseñas enormes a Argon2


class LoginSerializer(serializers.Serializer):
    correo = serializers.EmailField(max_length=254)
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
        fields = ["id", "nombre", "correo", "rol", "numero_nomina", "debe_cambiar_contrasena"]
        read_only_fields = fields


# --- gestión de usuarios (solo ADMIN) ------------------------------------------------------------

ORDENES = ("nombre", "correo", "rol", "activo", "creado_en")


class UsuarioAdminSerializer(serializers.ModelSerializer):
    """Lo que ve el administrador. Nunca el hash ni intentos_fallidos; bloqueado_hasta es de
    solo lectura (no existe desbloqueo manual)."""

    class Meta:
        model = Usuario
        fields = [
            "id",
            "nombre",
            "correo",
            "numero_nomina",
            "rol",
            "activo",
            "debe_cambiar_contrasena",
            "creado_en",
            "bloqueado_hasta",
        ]
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
    nombre = serializers.CharField(max_length=150)
    correo = serializers.EmailField(max_length=254)
    rol = serializers.ChoiceField(choices=Usuario.Rol.choices)
    numero_nomina = serializers.CharField(
        max_length=20, required=False, allow_null=True, allow_blank=True
    )


class EditarUsuarioSerializer(EstrictoSerializer):
    nombre = serializers.CharField(max_length=150, required=False)
    correo = serializers.EmailField(max_length=254, required=False)
    numero_nomina = serializers.CharField(
        max_length=20, required=False, allow_null=True, allow_blank=True
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
