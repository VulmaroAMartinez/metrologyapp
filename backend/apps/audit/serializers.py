from datetime import UTC

from django.utils import timezone
from rest_framework import serializers

from .models import TipoEvento
from .sanitize import solo_permitidos
from .services import SISTEMA, TAMANO_MAXIMO, TAMANO_POR_DEFECTO, FiltrosEventos


class EventoSerializer(serializers.Serializer):
    """Un evento tal como lo ve el administrador. `antes`/`despues` pasan por la lista blanca."""

    id = serializers.IntegerField()
    ocurrido_en = serializers.SerializerMethodField()
    ocurrido_en_local = serializers.SerializerMethodField()
    usuario = serializers.SerializerMethodField()
    tipo = serializers.CharField()
    descripcion = serializers.CharField()
    antes = serializers.SerializerMethodField()
    despues = serializers.SerializerMethodField()
    ip = serializers.CharField(allow_null=True)
    instrumento_id = serializers.IntegerField(allow_null=True)

    def get_ocurrido_en(self, evento):
        return evento.ocurrido_en.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def get_ocurrido_en_local(self, evento):
        return timezone.localtime(evento.ocurrido_en).isoformat()

    def get_usuario(self, evento):
        if evento.usuario_id is None:
            return None  # Sistema
        return {"id": evento.usuario_id, "nombre": evento.usuario.nombre}

    def get_antes(self, evento):
        return solo_permitidos(evento.antes)

    def get_despues(self, evento):
        return solo_permitidos(evento.despues)


class FiltrosBitacoraSerializer(serializers.Serializer):
    desde = serializers.DateField(required=False)
    hasta = serializers.DateField(required=False)
    usuario = serializers.CharField(required=False, max_length=20)
    instrumento_id = serializers.IntegerField(required=False, min_value=1)
    cursor = serializers.CharField(required=False, max_length=500)

    def validate_usuario(self, valor):
        if valor.lower() == SISTEMA:
            return SISTEMA
        if valor.isdigit() and int(valor) >= 1:
            return int(valor)
        raise serializers.ValidationError("Usa el id de un usuario o «sistema».")

    def validate(self, datos):
        if datos.get("desde") and datos.get("hasta") and datos["desde"] > datos["hasta"]:
            raise serializers.ValidationError({"hasta": ["No puede ser anterior a «desde»."]})
        return datos


def interpretar_consulta(parametros):
    """(filtros, texto_del_cursor, tamano) a partir de los parámetros de la URL; los inválidos
    lanzan ValidationError (400 datos_invalidos)."""
    simples = {
        clave: parametros[clave]
        for clave in FiltrosBitacoraSerializer().fields
        if clave in parametros
    }
    validador = FiltrosBitacoraSerializer(data=simples)
    validador.is_valid(raise_exception=True)
    datos = validador.validated_data

    # tipo admite repetirse (?tipo=A&tipo=B) y valores separados por coma (?tipo=A,B)
    tipos = tuple(
        dict.fromkeys(
            t.strip() for valor in parametros.getlist("tipo") for t in valor.split(",") if t.strip()
        )
    )
    desconocidos = [t for t in tipos if t not in TipoEvento.values]
    if desconocidos:
        raise serializers.ValidationError(
            {"tipo": [f"Tipo de evento desconocido: {desconocidos[0]}"]}
        )

    try:
        tamano = int(parametros.get("tamano", TAMANO_POR_DEFECTO))
    except ValueError:
        tamano = TAMANO_POR_DEFECTO
    if tamano < 1:
        tamano = TAMANO_POR_DEFECTO

    filtros = FiltrosEventos(
        desde=datos.get("desde"),
        hasta=datos.get("hasta"),
        usuario=datos.get("usuario"),
        tipos=tipos,
        instrumento_id=datos.get("instrumento_id"),
    )
    return filtros, datos.get("cursor"), min(tamano, TAMANO_MAXIMO)
