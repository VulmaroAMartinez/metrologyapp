"""Endpoints de gestión de usuarios: solo ADMIN (acción GESTIONAR_USUARIOS).

Sin borrado físico y sin desbloqueo manual. Solo el administrador asigna contraseñas: las
escribe en el cuerpo (`contrasena`) al crear y al restablecer, y nunca se devuelven.
"""

from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination

from apps.core.network import client_ip

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
from .views import respuesta

PERMISOS = [requiere(Accion.GESTIONAR_USUARIOS)]


class PaginadorUsuarios(PageNumberPagination):
    page_size = 25
    page_size_query_param = "tamano"
    max_page_size = 100
    page_query_param = "pagina"


def _error(error: services.ErrorDeUsuario):
    return respuesta({"codigo": error.codigo, "detalle": error.detalle}, status=error.estado)


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
    try:
        usuario = services.crear_usuario(
            request.user.pk, ip=client_ip(request), **entrada.validated_data
        )
    except services.ErrorDeUsuario as error:
        return _error(error)
    return respuesta(UsuarioAdminSerializer(usuario).data, status=201)


@api_view(["GET", "POST"])
@permission_classes(PERMISOS)
def usuarios(request):
    return _listar(request) if request.method == "GET" else _crear(request)


@api_view(["GET", "PATCH"])
@permission_classes(PERMISOS)
def usuario(request, pk):
    if request.method == "GET":
        objetivo = Usuario.objects.filter(pk=pk).first()
        if objetivo is None:
            raise NotFound("El usuario no existe.")
        return respuesta(UsuarioAdminSerializer(objetivo).data)
    entrada = EditarUsuarioSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    try:
        objetivo = services.editar_usuario(
            request.user.pk, pk, dict(entrada.validated_data), ip=client_ip(request)
        )
    except services.ErrorDeUsuario as error:
        return _error(error)
    return respuesta(UsuarioAdminSerializer(objetivo).data)


@api_view(["POST"])
@permission_classes(PERMISOS)
def cambiar_rol(request, pk):
    entrada = CambiarRolSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    try:
        objetivo = services.cambiar_rol(
            request.user.pk, pk, entrada.validated_data["rol"], ip=client_ip(request)
        )
    except services.ErrorDeUsuario as error:
        return _error(error)
    return respuesta(UsuarioAdminSerializer(objetivo).data)


@api_view(["POST"])
@permission_classes(PERMISOS)
def desactivar(request, pk):
    try:
        objetivo = services.desactivar_usuario(request.user.pk, pk, ip=client_ip(request))
    except services.ErrorDeUsuario as error:
        return _error(error)
    return respuesta(UsuarioAdminSerializer(objetivo).data)


@api_view(["POST"])
@permission_classes(PERMISOS)
def reactivar(request, pk):
    try:
        objetivo = services.reactivar_usuario(request.user.pk, pk, ip=client_ip(request))
    except services.ErrorDeUsuario as error:
        return _error(error)
    return respuesta(UsuarioAdminSerializer(objetivo).data)


@api_view(["POST"])
@permission_classes(PERMISOS)
def restablecer_contrasena(request, pk):
    entrada = RestablecerContrasenaSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    try:
        objetivo = services.restablecer_contrasena(
            request.user.pk, pk, entrada.validated_data["contrasena"], ip=client_ip(request)
        )
    except services.ErrorDeUsuario as error:
        return _error(error)
    return respuesta(UsuarioAdminSerializer(objetivo).data)
