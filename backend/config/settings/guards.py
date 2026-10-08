from django.core.exceptions import ImproperlyConfigured

EXAMPLE_MARKER = "EJEMPLO"  # prefijo de todos los secretos de .env.example
MIN_SECRET_KEY_LENGTH = 50


def validate_production(*, secret_key, db_password, allowed_hosts, debug):
    """Niega el arranque con configuración de ejemplo o insegura."""
    problems = []
    if debug:
        problems.append("DJANGO_DEBUG está activo")
    if not allowed_hosts:
        problems.append("DJANGO_ALLOWED_HOSTS está vacío")
    if secret_key.startswith(EXAMPLE_MARKER):
        problems.append("DJANGO_SECRET_KEY es el valor de ejemplo de .env.example")
    elif len(secret_key) < MIN_SECRET_KEY_LENGTH:
        problems.append(f"DJANGO_SECRET_KEY tiene menos de {MIN_SECRET_KEY_LENGTH} caracteres")
    if db_password.startswith(EXAMPLE_MARKER):
        problems.append("DB_PASSWORD es el valor de ejemplo de .env.example")
    if problems:
        raise ImproperlyConfigured("Configuración de producción rechazada: " + "; ".join(problems))
