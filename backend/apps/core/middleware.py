import ipaddress

from django.conf import settings

# Cabeceras que solo un proxy de confianza puede establecer.
FORWARDED_META_KEYS = (
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_FORWARDED_PROTO",
    "HTTP_X_FORWARDED_HOST",
    "HTTP_X_FORWARDED_PORT",
    "HTTP_FORWARDED",
    "HTTP_X_REAL_IP",
)


def parse_networks(cidrs):
    return tuple(ipaddress.ip_network(cidr.strip(), strict=False) for cidr in cidrs if cidr.strip())


def is_trusted(remote_addr, networks):
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in networks)


class TrustedProxyHeadersMiddleware:
    """Descarta X-Forwarded-* y similares cuando la conexión no viene del proxy (Caddy).

    Debe ir primero: lo que lean después Django (X-Forwarded-Proto) y DRF
    (X-Forwarded-For) ya está saneado.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.networks = parse_networks(settings.TRUSTED_PROXY_CIDRS)

    def __call__(self, request):
        if not is_trusted(request.META.get("REMOTE_ADDR", ""), self.networks):
            for key in FORWARDED_META_KEYS:
                request.META.pop(key, None)
        return self.get_response(request)


class NoStoreApiMiddleware:
    """Ninguna respuesta de /api/ se almacena en caché: llevan sesiones, datos de usuarios y
    bitácora. Va por fuera de todo (justo tras TrustedProxyHeadersMiddleware) para alcanzar
    también los errores (401, 403, 404, 405), el rechazo de CSRF y las redirecciones."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response["Cache-Control"] = "no-store"
        return response
