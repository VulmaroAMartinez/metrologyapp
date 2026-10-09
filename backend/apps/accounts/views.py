import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.middleware.csrf import get_token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.core.network import client_ip
from apps.core.ratelimit import check_rate

from . import services
from .permissions import Accion, requiere
from .serializers import CambiarContrasenaSerializer, LoginSerializer, UsuarioActualSerializer

logger = logging.getLogger(__name__)

CREDENCIALES_INVALIDAS = {
    "codigo": "credenciales_invalidas",
    "detalle": "Correo o contraseña incorrectos.",
}


def respuesta(data=None, status=200, headers=None):
    """Las respuestas de autenticación nunca se almacenan en caché."""
    response = Response(data, status=status, headers=headers)
    response["Cache-Control"] = "no-store"
    return response


def demasiados_intentos(*resultados):
    espera = max(r.retry_after for r in resultados if not r.allowed)
    logger.warning("Límite de tasa excedido; reintento en %s s", espera)
    return respuesta(
        {
            "codigo": "demasiados_intentos",
            "detalle": "Demasiados intentos. Espera e inténtalo de nuevo.",
        },
        status=429,
        headers={"Retry-After": str(espera)},
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def csrf(request):
    """Entrega el token y fija la cookie CSRF. Se llama antes de cualquier POST."""
    return respuesta({"csrfToken": get_token(request._request)})


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    correo = serializer.validated_data["correo"].strip().lower()

    # El límite se evalúa ANTES de calcular cualquier hash.
    ip = client_ip(request)
    por_ip_correo = check_rate(
        "login-ip-correo", f"{ip}|{correo}", settings.LOGIN_RATE_PER_IP_EMAIL
    )
    por_ip = check_rate("login-ip", ip, settings.LOGIN_RATE_PER_IP)
    if not (por_ip_correo.allowed and por_ip.allowed):
        return demasiados_intentos(por_ip_correo, por_ip)

    resultado = services.iniciar_sesion(request, correo, serializer.validated_data["contrasena"])
    if resultado.usuario is None:
        # Misma respuesta para correo inexistente, contraseña incorrecta, inactivo y bloqueado.
        return respuesta(CREDENCIALES_INVALIDAS, status=401)
    return respuesta(UsuarioActualSerializer(resultado.usuario).data)


@api_view(["POST"])
@permission_classes([AllowAny])
def logout(request):
    services.cerrar_sesion(request)
    return respuesta(status=204)


@api_view(["GET"])
@permission_classes([IsAuthenticated])  # cualquier rol autenticado
def me(request):
    return respuesta(UsuarioActualSerializer(request.user).data)


@api_view(["POST"])
@permission_classes([requiere(Accion.CAMBIAR_PROPIA_CONTRASENA)])  # solo ADMIN
def cambiar_contrasena(request):
    serializer = CambiarContrasenaSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    limite = check_rate("cambio-contrasena", str(request.user.pk), settings.LOGIN_RATE_PER_IP_EMAIL)
    if not limite.allowed:
        return demasiados_intentos(limite)

    try:
        usuario = services.cambiar_contrasena(
            request,
            request.user.pk,
            serializer.validated_data["contrasena_actual"],
            serializer.validated_data["contrasena_nueva"],
        )
    except services.ContrasenaActualIncorrecta:
        return respuesta(
            {
                "codigo": "contrasena_actual_incorrecta",
                "detalle": "La contraseña actual no es correcta.",
            },
            status=400,
        )
    except services.ContrasenaRepetida:
        return respuesta(
            {"codigo": "contrasena_repetida", "detalle": "La contraseña nueva debe ser distinta."},
            status=400,
        )
    except ValidationError as error:
        return respuesta({"codigo": "contrasena_invalida", "detalle": error.messages}, status=400)
    except services.ErrorDeUsuario as error:
        return respuesta({"codigo": error.codigo, "detalle": error.detalle}, status=error.estado)
    return respuesta(UsuarioActualSerializer(usuario).data)
