import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts import services
from apps.accounts.models import Usuario

VARIABLE_CONTRASENA = "CREAR_ADMIN_CONTRASENA"


class Command(BaseCommand):
    help = (
        "Crea un administrador (primer arranque). La contraseña se pide de forma interactiva; "
        f"{VARIABLE_CONTRASENA} solo es para instalaciones automatizadas. Queda obligado a "
        "cambiarla en su primer inicio de sesión."
    )

    def add_arguments(self, parser):
        parser.add_argument("--correo", required=True)
        parser.add_argument("--nombre", required=True)
        parser.add_argument("--numero-nomina", default=None)

    def handle(self, *args, **opciones):
        contrasena = os.environ.get(VARIABLE_CONTRASENA) or self._pedir_contrasena()
        try:
            usuario, _ = services.crear_usuario(
                None,  # el Sistema
                nombre=opciones["nombre"],
                correo=opciones["correo"],
                rol=Usuario.Rol.ADMIN,
                numero_nomina=opciones["numero_nomina"],
                contrasena=contrasena,
            )
        except services.ErrorDeUsuario as error:
            raise CommandError(error.detalle) from None
        # Nunca se imprime la contraseña.
        self.stdout.write(f"Administrador creado: {usuario.correo}. Deberá cambiar su contraseña.")

    def _pedir_contrasena(self) -> str:
        primera = getpass.getpass("Contraseña: ")
        if primera != getpass.getpass("Repite la contraseña: "):
            raise CommandError("Las contraseñas no coinciden.")
        return primera
