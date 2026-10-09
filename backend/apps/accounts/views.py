import logging

from django.conf import settings
from django.middleware.csrf import get_token
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.core.network import client_ip
from apps.core.openapi import (
    CONFLICTO,
    DATOS_INVALIDOS,
    DEMASIADAS_SOLICITUDES,
    NO_AUTENTICADO,
    PERMISO_DENEGADO,
    REINTENTAR,
    TIPO_NO_SOPORTADO,
)
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


@extend_schema(
    operation_id="auth_csrf",
    tags=["auth"],
    summary="Entrega el token CSRF y fija su cookie",
    responses=inline_serializer("Csrf", {"csrfToken": serializers.CharField()}),
)
@api_view(["GET"])
@permission_classes([AllowAny])
def csrf(request):
    """Entrega el token y fija la cookie CSRF. Se llama antes de cualquier POST."""
    return respuesta({"csrfToken": get_token(request._request)})


@extend_schema(
    operation_id="auth_login",
    tags=["auth"],
    summary="Inicia sesión",
    request=LoginSerializer,
    responses={
        200: UsuarioActualSerializer,
        400: DATOS_INVALIDOS,
        401: NO_AUTENTICADO,
        415: TIPO_NO_SOPORTADO,
        429: DEMASIADAS_SOLICITUDES,
    },
)
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

    # Un fallo de inicio de sesión NO lanza excepción: el contador y SESION_FALLIDA se confirman
    # con la transacción y la respuesta es 401 (ver services.iniciar_sesion).
    resultado = services.iniciar_sesion(request, correo, serializer.validated_data["contrasena"])
    if resultado.usuario is None:
        # Misma respuesta para correo inexistente, contraseña incorrecta, inactivo y bloqueado.
        return respuesta(CREDENCIALES_INVALIDAS, status=401)
    return respuesta(UsuarioActualSerializer(resultado.usuario).data)


@extend_schema(
    operation_id="auth_logout",
    tags=["auth"],
    summary="Cierra la sesión (idempotente)",
    request=None,
    responses={204: None},
)
@api_view(["POST"])
@permission_classes([AllowAny])
def logout(request):
    services.cerrar_sesion(request)
    return respuesta(status=204)


@extend_schema(
    operation_id="auth_me",
    tags=["auth"],
    summary="El usuario de la sesión",
    responses={200: UsuarioActualSerializer, 401: NO_AUTENTICADO},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])  # cualquier rol autenticado
def me(request):
    return respuesta(UsuarioActualSerializer(request.user).data)


@extend_schema(
    operation_id="auth_cambiar_contrasena",
    tags=["auth"],
    summary="Cambia la contraseña propia (solo ADMIN)",
    request=CambiarContrasenaSerializer,
    responses={
        200: UsuarioActualSerializer,
        400: DATOS_INVALIDOS,
        401: NO_AUTENTICADO,
        403: PERMISO_DENEGADO,
        409: CONFLICTO,
        415: TIPO_NO_SOPORTADO,
        429: DEMASIADAS_SOLICITUDES,
        503: REINTENTAR,
    },
)
@api_view(["POST"])
@permission_classes([requiere(Accion.CAMBIAR_PROPIA_CONTRASENA)])  # solo ADMIN
def cambiar_contrasena(request):
    serializer = CambiarContrasenaSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    limite = check_rate(
        "cambio-contrasena", str(request.user.pk), settings.PASSWORD_CHANGE_RATE_PER_USER
    )
    if not limite.allowed:
        return demasiados_intentos(limite)

    usuario = services.cambiar_contrasena(
        request,
        request.user.pk,
        serializer.validated_data["contrasena_actual"],
        serializer.validated_data["contrasena_nueva"],
    )
    return respuesta(UsuarioActualSerializer(usuario).data)
