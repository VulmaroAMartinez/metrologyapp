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
$COMPOSE --profile tools run --rm migrate
$COMPOSE up -d --build --wait
$COMPOSE --profile tools run --rm --no-deps -e PROXY_BASE_URL=http://proxy:8080 test \
  pytest --ds=config.settings.test -p no:cacheprovider -o addopts="" -v proxytests
