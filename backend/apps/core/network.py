from rest_framework.throttling import BaseThrottle


def client_ip(request) -> str:
    """IP del cliente según REST_FRAMEWORK['NUM_PROXIES'] (Caddy = 1).

    Las cabeceras X-Forwarded-* ya fueron saneadas por TrustedProxyHeadersMiddleware.
    """
    return BaseThrottle().get_ident(request)
