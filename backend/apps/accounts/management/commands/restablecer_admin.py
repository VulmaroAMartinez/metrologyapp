from django.core.management.base import BaseCommand, CommandError

from apps.accounts import services
from apps.accounts.management.prompts import pedir_contrasena
from apps.accounts.models import Usuario


class Command(BaseCommand):
    help = (
        "Recuperación: fija una contraseña nueva a un ADMINISTRADOR, desde el servidor. La pide "
        "de forma interactiva (dos veces, sin eco). No toca los contadores ni el bloqueo de la "
        "cuenta, cierra sus sesiones y deja un evento USUARIO_CONTRASENA_RESTABLECIDA del "
        "Sistema ('por comando de servidor')."
    )

    def add_arguments(self, parser):
        parser.add_argument("correo")

    def handle(self, *args, **opciones):
        correo = opciones["correo"].strip().lower()
        usuario = Usuario.objects.filter(correo=correo).first()
        if usuario is None or usuario.rol != Usuario.Rol.ADMIN:
            raise CommandError("No existe un administrador con ese correo.")
        contrasena = pedir_contrasena("Contraseña nueva")
        try:
            services.restablecer_contrasena(
                None, usuario.pk, contrasena, solo_rol=Usuario.Rol.ADMIN
            )
        except services.ErrorDeUsuario as error:
            raise CommandError(error.detalle) from None
        self.stdout.write(
            f"Contraseña restablecida para {usuario.correo}. Sus sesiones abiertas se cerraron; "
            "el bloqueo de la cuenta no se modificó."
        )
