from django.db import migrations

# Mismo esquema que `manage.py createcachetable`, pero versionado: lo crea migrator
# (dueño del esquema) y `app` recibe DML por los privilegios por defecto.
CREATE = """
CREATE TABLE django_cache (
    cache_key varchar(255) NOT NULL PRIMARY KEY,
    value text NOT NULL,
    expires timestamp with time zone NOT NULL
);
CREATE INDEX django_cache_expires ON django_cache (expires);
"""


class Migration(migrations.Migration):
    dependencies = []

    operations = [migrations.RunSQL(CREATE, reverse_sql="DROP TABLE django_cache")]
