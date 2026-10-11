"""Endpoints de gestión de usuarios: solo ADMIN (acción GESTIONAR_USUARIOS).

Sin borrado físico y sin desbloqueo manual. Solo el administrador asigna contraseñas: las
escribe en el cuerpo (`contrasena`) al crear y al restablecer, y nunca se devuelven.

Los errores de negocio son `ErrorDeUsuario` (una APIException): las vistas no los capturan, los
traduce el manejador de excepciones al sobre {codigo, detalle[, campos]}.
"""

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.core.constantes import TAMANO_MAXIMO, TAMANO_POR_DEFECTO
from apps.core.network import client_ip
from apps.core.openapi import (
    CONFLICTO,
    DATOS_INVALIDOS,
    NO_AUTENTICADO,
    NO_ENCONTRADO,
    PERMISO_DENEGADO,
    REINTENTAR,
    TIPO_NO_SOPORTADO,
)

from . import services
from .models import Usuario
from .permissions import Accion, requiere
from .serializers import (
    CambiarRolSerializer,
    CrearUsuarioSerializer,
    EditarUsuarioSerializer,
    FiltrosUsuariosSerializer,
    RestablecerContrasenaSerializer,
    UsuarioAdminSerializer,
)

PERMISOS = [requiere(Accion.GESTIONAR_USUARIOS)]
TAGS = ["usuarios"]
PaginaDeUsuarios = inline_serializer(
    "PaginaDeUsuarios",
    {
        "count": serializers.IntegerField(),
        "next": serializers.URLField(allow_null=True),
        "previous": serializers.URLField(allow_null=True),
        "results": UsuarioAdminSerializer(many=True),
    },
)
ERRORES_DE_ESCRITURA = {
    400: DATOS_INVALIDOS,
    401: NO_AUTENTICADO,
    403: PERMISO_DENEGADO,
    404: NO_ENCONTRADO,
    409: CONFLICTO,
    415: TIPO_NO_SOPORTADO,
    503: REINTENTAR,
}


class PaginadorUsuarios(PageNumberPagination):
    page_size = TAMANO_POR_DEFECTO
    page_size_query_param = "tamano"
    max_page_size = TAMANO_MAXIMO
    page_query_param = "pagina"


def _listar(request):
    filtros = FiltrosUsuariosSerializer(data=request.query_params.dict())
    filtros.is_valid(raise_exception=True)
    datos = filtros.validated_data
    usuarios = Usuario.objects.all()
    if "rol" in datos:
        usuarios = usuarios.filter(rol=datos["rol"])
    if "activo" in datos:
        usuarios = usuarios.filter(activo=datos["activo"])
    if datos.get("buscar"):
        texto = datos["buscar"].strip()
        usuarios = usuarios.filter(
            Q(nombre__icontains=texto)
            | Q(correo__icontains=texto)
            | Q(numero_nomina__icontains=texto)
        )
    usuarios = usuarios.order_by(datos.get("orden", "nombre"), "pk")  # pk: paginación estable
    paginador = PaginadorUsuarios()
    pagina = paginador.paginate_queryset(usuarios, request)
    return paginador.get_paginated_response(UsuarioAdminSerializer(pagina, many=True).data)


def _crear(request):
    entrada = CrearUsuarioSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    usuario = services.crear_usuario(
        request.user.pk, ip=client_ip(request), **entrada.validated_data
    )
    return Response(UsuarioAdminSerializer(usuario).data, status=201)


@extend_schema(
    methods=["GET"],
    operation_id="usuarios_listar",
    tags=TAGS,
    summary="Lista usuarios (paginación por páginas)",
    parameters=[
        FiltrosUsuariosSerializer,
        OpenApiParameter("pagina", int, description="Número de página (desde 1)."),
        OpenApiParameter("tamano", int, description="Elementos por página (máximo 100)."),
    ],
    responses={200: PaginaDeUsuarios, **ERRORES_DE_ESCRITURA},
)
@extend_schema(
    methods=["POST"],
    operation_id="usuarios_crear",
    tags=TAGS,
    summary="Crea un usuario con la contraseña que escribe el administrador",
    request=CrearUsuarioSerializer,
    responses={201: UsuarioAdminSerializer, **ERRORES_DE_ESCRITURA},
)
@api_view(["GET", "POST"])
@permission_classes(PERMISOS)
def usuarios(request):
    return _listar(request) if request.method == "GET" else _crear(request)


@extend_schema(
    methods=["GET"],
    operation_id="usuarios_obtener",
    tags=TAGS,
    summary="Un usuario",
    responses={200: UsuarioAdminSerializer, **ERRORES_DE_ESCRITURA},
)
@extend_schema(
    methods=["PATCH"],
    operation_id="usuarios_editar",
    tags=TAGS,
    summary="Edita nombre, correo y/o número de nómina",
    request=EditarUsuarioSerializer,
    responses={200: UsuarioAdminSerializer, **ERRORES_DE_ESCRITURA},
)
@api_view(["GET", "PATCH"])
@permission_classes(PERMISOS)
def usuario(request, pk):
    if request.method == "GET":
        objetivo = Usuario.objects.filter(pk=pk).first()
        if objetivo is None:
            raise NotFound("El usuario no existe.")
        return Response(UsuarioAdminSerializer(objetivo).data)
    entrada = EditarUsuarioSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    objetivo = services.editar_usuario(
        request.user.pk, pk, dict(entrada.validated_data), ip=client_ip(request)
    )
    return Response(UsuarioAdminSerializer(objetivo).data)


@extend_schema(
    operation_id="usuarios_cambiar_rol",
    tags=TAGS,
    summary="Cambia el rol (nunca el propio ni el del último administrador)",
    request=CambiarRolSerializer,
    responses={200: UsuarioAdminSerializer, **ERRORES_DE_ESCRITURA},
)
@api_view(["POST"])
@permission_classes(PERMISOS)
def cambiar_rol(request, pk):
    entrada = CambiarRolSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    objetivo = services.cambiar_rol(
        request.user.pk, pk, entrada.validated_data["rol"], ip=client_ip(request)
    )
    return Response(UsuarioAdminSerializer(objetivo).data)


@extend_schema(
    operation_id="usuarios_desactivar",
    tags=TAGS,
    summary="Desactiva al usuario y cierra sus sesiones",
    request=None,
    responses={200: UsuarioAdminSerializer, **ERRORES_DE_ESCRITURA},
)
@api_view(["POST"])
@permission_classes(PERMISOS)
def desactivar(request, pk):
    objetivo = services.desactivar_usuario(request.user.pk, pk, ip=client_ip(request))
    return Response(UsuarioAdminSerializer(objetivo).data)


@extend_schema(
    operation_id="usuarios_reactivar",
    tags=TAGS,
    summary="Reactiva al usuario (no desbloquea la cuenta)",
    request=None,
    responses={200: UsuarioAdminSerializer, **ERRORES_DE_ESCRITURA},
)
@api_view(["POST"])
@permission_classes(PERMISOS)
def reactivar(request, pk):
    objetivo = services.reactivar_usuario(request.user.pk, pk, ip=client_ip(request))
    return Response(UsuarioAdminSerializer(objetivo).data)


@extend_schema(
    operation_id="usuarios_restablecer_contrasena",
    tags=TAGS,
    summary="Fija una contraseña nueva a otro usuario y cierra sus sesiones",
    request=RestablecerContrasenaSerializer,
    responses={200: UsuarioAdminSerializer, **ERRORES_DE_ESCRITURA},
)
@api_view(["POST"])
@permission_classes(PERMISOS)
def restablecer_contrasena(request, pk):
    entrada = RestablecerContrasenaSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    objetivo = services.restablecer_contrasena(
        request.user.pk, pk, entrada.validated_data["contrasena"], ip=client_ip(request)
    )
    return Response(UsuarioAdminSerializer(objetivo).data)
