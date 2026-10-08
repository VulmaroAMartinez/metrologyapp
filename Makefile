# El compose vive en deploy/: Compose no lee el .env de la raíz, por eso --env-file explícito.
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file .env

.PHONY: help up down logs test lint ci secrets

help: ## Lista de comandos
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'

up: ## Levanta el entorno de desarrollo (requiere .env)
	@test -f .env || { echo "Falta .env: cp .env.example .env"; exit 1; }
	$(COMPOSE) up -d --build --wait

down: ## Detiene el entorno (conserva volúmenes)
	$(COMPOSE) down

logs: ## Sigue los logs
	$(COMPOSE) logs -f

secrets: ## Escaneo de secretos en el repo
	docker run --rm -v "$(CURDIR):/repo" -w //repo zricethezav/gitleaks:v8.30.1 detect --source . --config .gitleaks.toml --no-banner

# test, lint y ci se completan con cada tarea del sprint (backend, frontend, auditorías).
test: ## Pruebas (backend + frontend)
	@echo "Sin pruebas todavía"

lint: ## Valida compose y Caddyfile (se amplía con backend y frontend)
	docker compose -f deploy/docker-compose.yml --env-file .env.example config -q
	docker run --rm --env-file .env.example -v "$(CURDIR)/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.7-alpine caddy validate --config //etc/caddy/Caddyfile

ci: lint test secrets ## Todo lo anterior; falla ante cualquier problema
