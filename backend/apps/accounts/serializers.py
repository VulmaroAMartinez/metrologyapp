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
