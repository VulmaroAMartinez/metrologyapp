import os

from django.core.wsgi import get_wsgi_application

# Por defecto producción: arrancar sin configurar nada nunca debe dejar DEBUG activo.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_wsgi_application()
