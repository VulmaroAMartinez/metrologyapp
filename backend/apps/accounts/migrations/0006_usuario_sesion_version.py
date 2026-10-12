from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_correo_y_nomina_solo_ascii"),
    ]

    operations = [
        # Cambia la fórmula del hash de sesión: al desplegarla se cierran las sesiones existentes
        # una vez (docs/guia-instalacion.md).
        migrations.AddField(
            model_name="usuario",
            name="sesion_version",
            field=models.IntegerField(default=0),
        ),
    ]
