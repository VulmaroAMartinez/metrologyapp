import getpass

from django.core.management.base import CommandError


def pedir_contrasena(etiqueta: str = "Contraseña") -> str:
    """Pide la contraseña dos veces, sin eco. Nunca se imprime ni se registra."""
    primera = getpass.getpass(f"{etiqueta}: ")
    if primera != getpass.getpass(f"Repite la {etiqueta.lower()}: "):
        raise CommandError("Las contraseñas no coinciden.")
    return primera
