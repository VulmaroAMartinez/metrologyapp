# Sistema de control de instrumentos de metrología

Definición completa: [docs/DEFINICION.md](docs/DEFINICION.md). Este README crece con cada sprint.

## Arranque desde cero (desarrollo)

Requisitos: Docker con Compose y GNU Make.

```sh
cp .env.example .env     # todos los valores son de ejemplo; cámbialos fuera de desarrollo
make up                  # levanta db, api, web y proxy, y aplica las migraciones
```

El sistema queda en <http://localhost:8080> (solo `127.0.0.1`). El compose vive en `deploy/`, así que
Compose no lee el `.env` de la raíz por sí solo: el `Makefile` pasa `--env-file .env` en cada llamada.
Si ejecutas `docker compose` a mano, usa `docker compose -f deploy/docker-compose.yml --env-file .env ...`.

| Comando | Qué hace |
| --- | --- |
| `make up` / `make down` | Levanta (con migraciones) / detiene el entorno; los volúmenes se conservan |
| `make migrate` | Aplica migraciones con el rol `migrator` |
| `make test` | Pruebas del backend (pytest, cobertura mínima 85 %) |
| `make lint` | ruff + validación del compose y del Caddyfile |
| `make fmt` | Formatea el backend con ruff |
| `make lock` | Regenera `backend/requirements*.txt` con hashes |
| `make secrets` | Escaneo de secretos con gitleaks |
| `make ci` | lint + pruebas + secretos |

## Roles de base de datos

| Rol | Uso | Cómo se conecta |
| --- | --- | --- |
| `postgres` | Solo inicialización del volumen y healthcheck | No lo usa ninguna aplicación |
| `migrator` | Dueño del esquema: migraciones y pruebas (puede crear la BD `test_*`) | `make migrate` y `make test` fijan `DB_USER=migrator`, `DB_PASSWORD=$MIGRATOR_PASSWORD` |
| `app` | El servidor: SELECT/INSERT/UPDATE/DELETE, sin DDL | El servicio `api` fija `DB_USER=app`, `DB_PASSWORD=$APP_PASSWORD` |

Django lee la conexión de `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` y `DB_PASSWORD`; el rol se elige solo con
esas dos últimas. Los roles se crean una sola vez, al inicializar el volumen, con `deploy/db/init.sql`.

### Cambiar las contraseñas después de la primera inicialización

Editar `.env` **no** cambia las contraseñas de una base ya creada: `init.sql` solo corre con el volumen vacío.
Para cambiarlas:

1. Con el sistema arriba, abre `psql` dentro del contenedor (entra por socket local; no pide contraseña) y
   cambia cada rol con `\password`, que no deja la clave en el historial del shell:
   ```sh
   docker compose -f deploy/docker-compose.yml --env-file .env exec db psql -U postgres -d metrologia
   \password app
   \password migrator
   \password postgres
   ```
2. Pon los mismos valores nuevos en `.env` (`APP_PASSWORD`, `MIGRATOR_PASSWORD`, `POSTGRES_PASSWORD`).
3. `make down && make up` para que `api` use la nueva clave.

Si el nombre de la base no es `metrologia`, cambia `-d metrologia` por el valor de `DB_NAME`.
(En desarrollo también puedes borrar el volumen con `docker compose ... down -v` y recrear todo; **se pierden los datos**.)

## Producción

`config.settings.prod` se niega a arrancar si: `DJANGO_DEBUG` está activo, `DJANGO_ALLOWED_HOSTS` está vacío,
`DJANGO_SECRET_KEY` o `DB_PASSWORD` conservan el valor de ejemplo (prefijo `EJEMPLO`) o la clave
secreta tiene menos de 50 caracteres. El esquema OpenAPI (`/api/v1/schema/`) no se sirve en producción.
