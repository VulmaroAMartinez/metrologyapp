# Decisiones del sprint 1

Se completa al terminar el sprint. Los valores provisionales se listan para que Vul los confirme.

## Versiones (verificadas el 8-oct-2026)
- PostgreSQL 18.6 (no existe LTS; es la mayor vigente, soporte hasta 2030).
- Django 5.2 LTS (5.2.18), soporta Python 3.10–3.14. El LTS 6.2 sale en abril de 2027.
- Python 3.14.8 (3.13 entra en solo-seguridad en oct-2026). Todas las dependencias tienen wheels para 3.14; la instalación usa `--only-binary` y `--require-hashes`.
- Node 24.21.0 (LTS vigente; Node 26 pasa a LTS el 28-oct-2026). Caddy 2.11.7.
- Imágenes con tag exacto y digest sha256.

## Decisiones técnicas
- Sin `django.contrib.auth/sessions/contenttypes` en el commit 3: se agregan junto con el modelo de usuario propio para que la primera migración ya lo use.
- `/api/v1/health/` devuelve solo `{"status": "ok"}` (503 `{"status": "error"}` si la BD no responde).
- El esquema OpenAPI se sirve solo con `OPENAPI_SCHEMA_ENABLED=True` (dev); en producción responde 404.
- La validación de producción solo comprueba `DB_PASSWORD` (la única clave de BD que ve el servidor, rol `app`).
- **Sin `django.contrib.admin` en la v1**: la administración se hace con la API y la pantalla de usuarios; no existe `is_staff` ni `is_superuser`. El único administrador es un Usuario con rol ADMIN.
- Se instalan `contenttypes`, `auth` y `sessions` (las dos primeras por requisito de Django, `sessions` para la sesión del commit 5). `auth` crea sus tablas `auth_group` y `auth_permission`, que no se usan.
- Usuario: correo único sin distinguir mayúsculas (`UNIQUE` sobre `Lower(correo)`) y guardado en minúsculas; `rol` con CheckConstraint; `intentos_fallidos >= 0`. El manager exige rol y no crea superusuarios.
- Argon2 es el único hasher, con los parámetros por defecto de Django (m=100 MiB, t=2, p=8): cada verificación usa unos 100 MiB de memoria transitoria; con 3 workers de gunicorn, dimensionar la memoria del contenedor en consecuencia. Las pruebas usan MD5 (`config.settings.test`) y una prueba verifica la configuración real.
- **Dimensionamiento de memoria de `api`** (medido el 8-oct-2026): cada hash/verificación Argon2 suma ~100 MiB transitorios; un proceso de Python en reposo ocupa ~45 MiB y la API en reposo con 3 workers ~175 MiB. Peor caso con 3 hashes simultáneos ≈ 480 MiB. Límite fijado en `mem_limit: 768m` (sin swap, `pids_limit: 256`). Si se suben workers o parámetros de Argon2, recalcular: `workers × 100 MiB + 175 MiB + holgura`.
- Los datos sensibles no entran a logs: `django.db.backends` fijado en INFO (a nivel DEBUG registraría el hash en el SQL).
- Migraciones y pruebas con el rol `migrator`; el servidor con `app`.
- En prod, `SECURE_SSL_REDIRECT` exime `/api/v1/health/` para que el healthcheck interno (HTTP) no reciba 301.

## Valores provisionales para confirmar
- Longitud mínima de contraseña: 10 (`PASSWORD_MIN_LENGTH`).

## Preguntas abiertas
- `numero_nomina`: DEFINICION §5 lo lista en Usuario pero no dice si es obligatorio. Queda **opcional**: `NULL` cuando no existe (nunca cadena vacía) y **único cuando tiene valor** (restricción parcial). Confirmar si debe ser obligatorio.

## Cambios que Vul debe hacer a docs/DEFINICION.md (archivo protegido)
- (se irá llenando)
