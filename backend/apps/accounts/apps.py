from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from . import openapi  # noqa: F401 - registra la extensión de drf-spectacular
        from .middleware import marcar_inicio
        from .services import dummy_hash

        user_logged_in.connect(marcar_inicio, dispatch_uid="accounts.marcar_inicio_de_sesion")
        dummy_hash()  # precalcula el hash ficticio del login antes de la primera petición
