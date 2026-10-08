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
- Migraciones y pruebas con el rol `migrator`; el servidor con `app`.
- En prod, `SECURE_SSL_REDIRECT` exime `/api/v1/health/` para que el healthcheck interno (HTTP) no reciba 301.

## Cambios que Vul debe hacer a docs/DEFINICION.md (archivo protegido)
- (se irá llenando)
