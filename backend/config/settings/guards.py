import ipaddress

from django.core.exceptions import ImproperlyConfigured

EXAMPLE_MARKER = "EJEMPLO"  # prefijo de todos los secretos de .env.example
MIN_SECRET_KEY_LENGTH = 50

# Valor por defecto (desarrollo): todos los rangos privados. Producción lo rechaza.
DEFAULT_TRUSTED_PROXY_CIDRS = [
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
]
# Redes de proxy más anchas que esto (IPv4 /16, IPv6 /64) dejan confiar en X-Forwarded-* a
# cualquier máquina de la red. Una red de Docker típica es /16 o más estrecha.
MIN_PREFIX_IPV4 = 16
MIN_PREFIX_IPV6 = 64
IPV4_MAPEADO = ipaddress.ip_network("::ffff:0:0/96")


def _problemas_proxies(cidrs):
    limpios = [c.strip() for c in cidrs if c.strip()]
    if not limpios:
        return ["TRUSTED_PROXY_CIDRS está vacío (se perdería la IP real de los clientes)"]
    if sorted(limpios) == sorted(DEFAULT_TRUSTED_PROXY_CIDRS):
        return [
            "TRUSTED_PROXY_CIDRS conserva el valor por defecto (todos los rangos privados); "
            "acótalo a la subred de Docker (docs/guia-instalacion.md)"
        ]
    problemas = []
    for cidr in limpios:
        try:
            red = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            problemas.append(f"TRUSTED_PROXY_CIDRS contiene un CIDR inválido: {cidr!r}")
            continue
        minimo = MIN_PREFIX_IPV4 if red.version == 4 else MIN_PREFIX_IPV6
        if red.prefixlen < minimo:
            problemas.append(
                f"TRUSTED_PROXY_CIDRS: {cidr} es demasiado amplio (máximo /{minimo}); "
                "acótalo a la subred de Docker (docs/guia-instalacion.md)"
            )
        elif red.version == 6 and red.overlaps(IPV4_MAPEADO):
            # ::ffff:a.b.c.d es una IPv4 escrita como IPv6: abarcaría direcciones IPv4 enteras.
            problemas.append(
                f"TRUSTED_PROXY_CIDRS: {cidr} es un rango IPv4 mapeado en IPv6; "
                "usa el CIDR IPv4 de la subred de Docker"
            )
        elif red.is_global:
            problemas.append(
                f"TRUSTED_PROXY_CIDRS: {cidr} no es una red privada; el proxy debe estar en la "
                "red interna de Docker (docs/guia-instalacion.md)"
            )
    return problemas


def validate_production(*, secret_key, db_password, allowed_hosts, debug, trusted_proxy_cidrs):
    """Niega el arranque con configuración de ejemplo o insegura."""
    problems = []
    if debug:
        problems.append("DJANGO_DEBUG está activo")
    if not allowed_hosts:
        problems.append("DJANGO_ALLOWED_HOSTS está vacío")
    elif "*" in allowed_hosts:
        problems.append("DJANGO_ALLOWED_HOSTS contiene '*' (acepta cualquier cabecera Host)")
    if secret_key.startswith(EXAMPLE_MARKER):
        problems.append("DJANGO_SECRET_KEY es el valor de ejemplo de .env.example")
    elif len(secret_key) < MIN_SECRET_KEY_LENGTH:
        problems.append(f"DJANGO_SECRET_KEY tiene menos de {MIN_SECRET_KEY_LENGTH} caracteres")
    if not db_password.strip():
        problems.append("DB_PASSWORD está vacío")
    elif db_password.startswith(EXAMPLE_MARKER):
        problems.append("DB_PASSWORD es el valor de ejemplo de .env.example")
    problems += _problemas_proxies(trusted_proxy_cidrs)
    if problems:
        raise ImproperlyConfigured("Configuración de producción rechazada: " + "; ".join(problems))
