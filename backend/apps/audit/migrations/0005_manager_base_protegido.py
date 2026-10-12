from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0004_indice_cursor_bitacora"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="evento",
            options={"base_manager_name": "objects"},
        ),
    ]
