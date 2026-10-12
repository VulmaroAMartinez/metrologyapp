# Sistema de control de instrumentos de metrología

Definición completa: [docs/DEFINICION.md](docs/DEFINICION.md). Este README crece con cada sprint.

## Arranque desde cero (desarrollo)

Requisitos: Docker con Compose y GNU Make.

```sh
cp .env.example .env     # opcional: cualquier `make` lo crea si falta; los valores son de ejemplo, cámbialos fuera de desarrollo
make up                  # levanta db, api, web y proxy, y aplica las migraciones
```

El sistema queda en <http://localhost:8080> (solo `127.0.0.1`). El compose vive en `deploy/`, así que
Compose no lee el `.env` de la raíz por sí solo: el `Makefile` pasa `--env-file .env` en cada llamada.
Si ejecutas `docker compose` a mano, usa `docker compose -f deploy/docker-compose.yml --env-file .env ...`.

| Comando | Qué hace |
| --- | --- |
| `make up` / `make down` | Levanta (con migraciones) / detiene el entorno; los volúmenes se conservan |
| `make migrate` | Aplica migraciones con el rol `migrator` |
| `make test` | Pruebas del backend (pytest, cobertura mínima 95 %, con ramas) |
| `make lint` | ruff + validación del compose y del Caddyfile |
| `make fmt` | Formatea el backend con ruff |
| `make lock` | Regenera `backend/requirements*.txt` con hashes |
| `make schema` | Valida el contrato OpenAPI (`spectacular --validate --fail-on-warn`) |
| `make audit` | Auditoría de dependencias (pip-audit) contra los archivos con hashes; falla si hay vulnerabilidades |
| `make test-proxy` | Pruebas contra el stack real (Caddy, Django, PostgreSQL): cabeceras falsificadas, cabeceras de seguridad, cookies, flujos de usuarios y verificación de contenedores |
| `make check-containers` | Verifica la seguridad de los contenedores del entorno de desarrollo en ejecución |
| `make secrets` | Escaneo de secretos con gitleaks |
| `make ci` | lint + contrato OpenAPI + auditoría + pruebas + pruebas contra el stack real + secretos |
| `.github/workflows/ci.yml` | Lo mismo que `make ci`, en tres trabajos paralelos (calidad, pruebas, proxy y esquema) en push y pull request a `main` |

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

## Autenticación (API)

Sesión por cookie bajo `/api/v1/auth/`: `GET csrf/` (token), `POST login/`, `POST logout/`, `GET me/`
y `POST cambiar-contrasena/`. Todo `POST` lleva la cabecera `X-CSRFToken` con el token de `csrf/`
(que cambia al iniciar sesión). Los valores de bloqueo, límite de tasa e inactividad son variables
de `.env` (ver `.env.example` y `docs/decisiones.md`). Con el stack arriba puedes crear un usuario de
prueba: `docker compose -f deploy/docker-compose.yml --env-file .env --profile tools run --rm migrate python manage.py shell`.

## Primer administrador y gestión de usuarios

```sh
docker compose -f deploy/docker-compose.yml --env-file .env --profile tools run --rm migrate \
  python manage.py crear_admin --correo admin@empresa.com --nombre "Nombre Apellido"
```

Pide la contraseña de forma interactiva (`CREAR_ADMIN_CONTRASENA` solo para instalaciones
automatizadas: bórrala del `.env` después de usarla). **Solo el administrador asigna y restablece
contraseñas**: las escribe en `/api/v1/usuarios/` (campo `contrasena`, pasa por los validadores y nunca
se devuelve) y los técnicos y espectadores no pueden cambiar la suya. Mantén **dos administradores
activos**. Para recuperar un administrador desde el servidor:

```sh
docker compose -f deploy/docker-compose.yml --env-file .env --profile tools run --rm migrate \
  python manage.py restablecer_admin admin@empresa.com
```

No hay borrado ni desbloqueo manual de cuentas.

## Bitácora

`GET /api/v1/bitacora/eventos/` (solo administrador): paginación por cursor (`tamano`, máximo 100; sigue
el `next` de la respuesta) y filtros `desde`, `hasta` (fechas locales `AAAA-MM-DD`), `usuario` (id o `sistema`),
`tipo` (repetible o separado por comas) e `instrumento_id`. Es de solo lectura y consultarla no genera eventos.

## Producción

**El sistema se instala solo en red interna o VPN, nunca expuesto directo a internet** (el bloqueo de cuentas y el límite de tasa asumen un atacante con pocas IPs).

`config.settings.prod` se niega a arrancar si: `DJANGO_DEBUG` está activo, `DJANGO_ALLOWED_HOSTS` está vacío,
`DJANGO_SECRET_KEY` o `DB_PASSWORD` conservan el valor de ejemplo (prefijo `EJEMPLO`) o la clave
secreta tiene menos de 50 caracteres, o si `TRUSTED_PROXY_CIDRS` es demasiado amplio (ver
`docs/guia-instalacion.md`). El esquema OpenAPI (`/api/v1/schema/`) no se sirve en producción.
