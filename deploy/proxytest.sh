#!/bin/sh
# Pruebas contra el stack real (Caddy + Django + PostgreSQL) en un proyecto aparte.
# Genera secretos aleatorios en cada corrida, no toca tu .env ni tu base de desarrollo
# y borra todo al terminar, pase o falle.
set -eu
cd "$(dirname "$0")/.."

ENV_FILE=.proxytest.env
rand() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

cat > "$ENV_FILE" <<EOF
DB_NAME=metrologia
POSTGRES_PASSWORD=$(rand 32)
MIGRATOR_PASSWORD=$(rand 32)
APP_PASSWORD=$(rand 32)
DJANGO_SECRET_KEY=$(rand 64)
DJANGO_ALLOWED_HOSTS=proxy,localhost,127.0.0.1
HTTP_PORT=18080
EOF

COMPOSE="docker compose -p metrologia-proxytest -f deploy/docker-compose.yml -f deploy/docker-compose.proxytest.yml --env-file $ENV_FILE"
cleanup() {
  $COMPOSE --profile tools down -v >/dev/null 2>&1 || true
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

$COMPOSE up -d --build --wait db
$COMPOSE --profile tools run --rm --build migrate
# Primer administrador, como en una instalación real (crear_admin; entra directo, sin cambio forzado).
ADMIN_CORREO=admin-proxytest@empresa.test
ADMIN_CLAVE=$(rand 24)
# La clave viaja por el entorno del comando (`-e NOMBRE` sin valor): no queda en la lista de procesos.
CREAR_ADMIN_CONTRASENA="$ADMIN_CLAVE" $COMPOSE --profile tools run --rm --no-deps \
  -e CREAR_ADMIN_CONTRASENA migrate \
  python manage.py crear_admin --correo "$ADMIN_CORREO" --nombre "Admin Proxytest"
$COMPOSE up -d --build --wait
sh deploy/verificar_contenedores.sh metrologia-proxytest
# PROXYTEST_REPETIR=10 repite las pruebas sobre el MISMO stack (para comprobar que no son
# intermitentes); PROXYTEST_PYTEST_ARGS="-k forged" las filtra. Se detiene en la primera falla.
CORRIDAS=${PROXYTEST_REPETIR:-1}
corrida=1
while [ "$corrida" -le "$CORRIDAS" ]; do
  echo "== proxytests: corrida $corrida de $CORRIDAS"
  # shellcheck disable=SC2086
  PROXYTEST_ADMIN_CONTRASENA="$ADMIN_CLAVE" $COMPOSE --profile tools run --rm --build --no-deps \
    -e PROXY_BASE_URL=http://proxy:8080 -e PROXYTEST_ADMIN_CORREO="$ADMIN_CORREO" \
    -e PROXYTEST_ADMIN_CONTRASENA test \
    pytest --ds=config.settings.test -p no:cacheprovider -o addopts="" -v proxytests \
    ${PROXYTEST_PYTEST_ARGS:-}
  if [ "$corrida" -lt "$CORRIDAS" ]; then
    # Cada corrida agota el límite por IP (ventana fija de 60 s): la siguiente empieza en una
    # ventana nueva, igual que una corrida única de `make test-proxy`.
    espera=$((61 - $(date +%s) % 60))
    echo "== esperando ${espera}s al comienzo de la ventana siguiente"
    sleep "$espera"
  fi
  corrida=$((corrida + 1))
done
