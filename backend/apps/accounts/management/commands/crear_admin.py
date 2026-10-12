import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts import services
from apps.accounts.management.prompts import pedir_contrasena
from apps.accounts.models import Usuario

VARIABLE_CONTRASENA = "CREAR_ADMIN_CONTRASENA"


class Command(BaseCommand):
    help = (
        "Crea un administrador. La contraseña se pide de forma interactiva (dos veces, sin eco). "
        f"{VARIABLE_CONTRASENA} es solo para instalaciones automatizadas: el script de "
        "instalación debe borrarla del .env después de usarla."
    )

    def add_arguments(self, parser):
        parser.add_argument("--correo", required=True)
        parser.add_argument("--nombre", required=True)
        parser.add_argument("--numero-nomina", default=None)

    def handle(self, *args, **opciones):
        contrasena = os.environ.get(VARIABLE_CONTRASENA)
        if contrasena:
            self.stderr.write(
                f"Aviso: se usó {VARIABLE_CONTRASENA}. Bórrala del .env y del entorno ahora."
            )
        else:
            contrasena = pedir_contrasena()
        try:
            usuario = services.crear_usuario(
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
        self.stdout.write(f"Administrador creado: {usuario.correo}.")
