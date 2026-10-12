"""Vida máxima absoluta de la sesión.

La sesión se renueva con cada petición (ventana deslizante por inactividad), pero nunca más
allá de SESSION_ABSOLUTE_HOURS desde el inicio de sesión: así una sesión robada o olvidada no
se mantiene viva indefinidamente con actividad. Expirar no genera evento (CLAUDE.md: no se
registran cierres ni expiraciones de sesión).
"""

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

CLAVE_INICIO_SESION = "iniciada_en"


def marcar_inicio(sender, request, user, **kwargs):
    """Receptor de user_logged_in: sella el inicio de la sesión (login real y force_login)."""
    request.session[CLAVE_INICIO_SESION] = timezone.now().timestamp()


def _vencida(request) -> bool:
    iniciada = request.session.get(CLAVE_INICIO_SESION)
    if isinstance(iniciada, bool) or not isinstance(iniciada, int | float):
        return True  # sin marca de inicio (sesión anterior a este control): no se acepta
    return timezone.now().timestamp() - iniciada >= settings.SESSION_ABSOLUTE_HOURS * 3600


class AbsoluteSessionMiddleware:
    """Debe ir después de AuthenticationMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and _vencida(request):
            request.session.flush()  # borra la sesión en el servidor y la cookie
            request.user = AnonymousUser()
        return self.get_response(request)
