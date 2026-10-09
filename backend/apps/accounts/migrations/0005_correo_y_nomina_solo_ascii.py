from django.db import migrations, models

NO_ASCII = r"[^\x01-\x7f]"
SOLO_ASCII = r"^[\x01-\x7f]+$"


def verificar_solo_ascii(apps, schema_editor):
    """Decisión de Vul: correo y numero_nomina solo ASCII. Si ya hay usuarios con otros
    caracteres, la migración se detiene con un mensaje claro en vez de fallar a medias: hay que
    corregirlos (no hay borrado físico de usuarios) antes de aplicarla."""
    Usuario = apps.get_model("accounts", "Usuario")
    cantidad = (
        Usuario.objects.filter(
            models.Q(correo__regex=NO_ASCII) | models.Q(numero_nomina__regex=NO_ASCII)
        )
    ).count()
    if cantidad:
        raise RuntimeError(
            f"Hay {cantidad} usuarios con caracteres no ASCII en correo o numero_nomina; "
            "corrígelos antes de aplicar esta migración."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_quitar_cambio_forzado"),
    ]

    operations = [
        migrations.RunPython(verificar_solo_ascii, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="usuario",
            constraint=models.CheckConstraint(
                condition=models.Q(("correo__regex", SOLO_ASCII)),
                name="usuario_correo_ascii",
            ),
        ),
        migrations.AddConstraint(
            model_name="usuario",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("numero_nomina__isnull", True),
                    ("numero_nomina__regex", SOLO_ASCII),
                    _connector="OR",
                ),
                name="usuario_nomina_ascii",
            ),
        ),
    ]
