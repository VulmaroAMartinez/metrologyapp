#!/bin/sh
# Se ejecuta una sola vez, al crear el volumen de la base de datos.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v dbname="$POSTGRES_DB" \
  -v migrator_pw="$MIGRATOR_PASSWORD" \
  -v app_pw="$APP_PASSWORD" \
  -f /opt/db/init.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -f /opt/db/privileges.sql
