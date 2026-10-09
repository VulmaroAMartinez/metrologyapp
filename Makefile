# El compose vive en deploy/: Compose no lee el .env de la raíz, por eso --env-file explícito.
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file .env
MIGRATE := $(COMPOSE) --profile tools run --rm --build migrate

.PHONY: help up down logs migrate fmt lock audit check-containers test test-proxy lint ci secrets

help: ## Lista de comandos
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'

up: ## Levanta el entorno de desarrollo y aplica migraciones (requiere .env)
	@test -f .env || { echo "Falta .env: cp .env.example .env"; exit 1; }
	$(COMPOSE) up -d --build --wait db
	$(MIGRATE)
	$(COMPOSE) up -d --build --wait

migrate: ## Aplica migraciones con el rol migrator
	$(MIGRATE)

down: ## Detiene el entorno (conserva volúmenes)
	$(COMPOSE) down

logs: ## Sigue los logs
	$(COMPOSE) logs -f

PYIMG := python:3.14.8-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83

fmt: ## Formatea el backend con ruff (escribe en backend/)
	docker compose -f deploy/docker-compose.yml --env-file .env.example --profile tools build test
	docker run --rm --user root -v "$(CURDIR)/backend:/app" -w //app metrologia-test sh -c "ruff check --no-cache --fix . ; ruff format --no-cache ."

lock: ## Regenera backend/requirements*.txt con hashes (solo wheels)
	docker run --rm -v "$(CURDIR)/backend:/work" -w //work $(PYIMG) sh -c "pip install -q pip-tools==7.6.2 && 	pip-compile -q --generate-hashes --allow-unsafe --strip-extras --pip-args=--only-binary=:all: -o requirements.txt requirements.in && 	pip-compile -q --generate-hashes --allow-unsafe --strip-extras --pip-args=--only-binary=:all: -o requirements-dev.txt requirements-dev.in"

secrets: ## Escaneo de secretos: historial git y archivos del directorio de trabajo
	docker run --rm -v "$(CURDIR):/repo" -w //repo zricethezav/gitleaks:v8.30.1 detect --source . --config .gitleaks.toml --no-banner
	docker run --rm -v "$(CURDIR):/repo" -w //repo zricethezav/gitleaks:v8.30.1 dir . --config .gitleaks.toml --no-banner

# test, lint y ci se completan con cada tarea del sprint (backend, frontend, auditorías).
test: ## Pruebas (backend con pytest; frontend se suma después)
	$(COMPOSE) --profile tools run --rm --build test

audit: ## Auditoría de dependencias (pip-audit) contra los archivos con hashes; falla si hay vulnerabilidades
	docker compose -f deploy/docker-compose.yml --env-file .env.example --profile tools run --rm --build --no-deps -e HOME=//tmp -e XDG_CACHE_HOME=//tmp test sh -c "pip-audit -r requirements.txt --require-hashes --disable-pip && pip-audit -r requirements-dev.txt --require-hashes --disable-pip"

check-containers: ## Verifica la seguridad de los contenedores del entorno de desarrollo en ejecución
	sh deploy/verificar_contenedores.sh metrologia

test-proxy: ## Pruebas contra el stack real (Caddy + Django) con cabeceras falsificadas
	sh deploy/proxytest.sh

lint: ## Valida compose y Caddyfile, y ruff del backend
	docker compose -f deploy/docker-compose.yml --env-file .env.example --profile tools run --rm --build --no-deps test sh -c "ruff check --no-cache . && ruff format --check --no-cache ."
	docker compose -f deploy/docker-compose.yml --env-file .env.example config -q
	docker run --rm --env-file .env.example -v "$(CURDIR)/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.7-alpine caddy validate --config //etc/caddy/Caddyfile

ci: lint audit test test-proxy secrets ## Todo lo anterior; falla ante cualquier problema
