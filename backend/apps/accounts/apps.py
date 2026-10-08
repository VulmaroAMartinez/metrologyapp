from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        from .services import dummy_hash

        dummy_hash()  # precalcula el hash ficticio del login antes de la primera petición
