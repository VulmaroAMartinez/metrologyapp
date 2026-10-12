from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="usuario",
            name="numero_nomina",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        # Los "" existentes pasan a NULL antes de crear la restricción única parcial.
        migrations.RunSQL(
            "UPDATE accounts_usuario SET numero_nomina = NULL WHERE btrim(numero_nomina) = ''",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddConstraint(
            model_name="usuario",
            constraint=models.UniqueConstraint(
                condition=models.Q(("numero_nomina__isnull", False)),
                fields=("numero_nomina",),
                name="usuario_nomina_unica",
            ),
        ),
    ]
