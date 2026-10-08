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
- **Autenticación** (commit 5): sesión en base de datos (cerrar sesión la borra en el servidor), ventana deslizante de inactividad, clave de sesión rotada al iniciar sesión. Cookies `HttpOnly`, `SameSite=Lax`; en prod `Secure` y prefijo `__Host-` (sin Domain, Path=/). La cookie CSRF también es `HttpOnly`: el token se obtiene de `GET /api/v1/auth/csrf/` y **rota al iniciar sesión** (el cliente debe pedirlo de nuevo). CSRF se exige en todo POST, también anónimo (login incluido). CORS cerrado: no se instala `django-cors-headers`; mismo origen vía Caddy.
- **Login indistinguible**: correo inexistente, contraseña incorrecta, usuario inactivo, cuenta bloqueada y usuario sin contraseña devuelven 401 con el mismo cuerpo y cabeceras, y siempre ejecutan una verificación de hash (contra un hash ficticio precalculado cuando no hay usuario elegible). El motivo real solo va a logs internos (sin correo ni contraseña).
- **Contador de fallos**: un único `UPDATE` atómico (sin leer-modificar-guardar). Al llegar al límite se fija `bloqueado_hasta` y el contador queda en el límite mientras dure el bloqueo; los intentos durante el bloqueo no lo alargan; al vencer, el siguiente fallo abre un ciclo nuevo (cuenta 1) y un login correcto lo reinicia a 0.
- **Límite de tasa**: ventana fija de 1 minuto sobre la caché en base de datos (`django_cache`, tabla creada por migración), una ranura por intento con `cache.add` (atómico entre workers; `cache.incr` no lo es). Se evalúa antes de calcular cualquier hash y responde 429 con `Retry-After`. Limitación conocida: una ventana fija permite hasta el doble del límite alrededor del cambio de minuto. Los identificadores se guardan hasheados.
- **IP real**: `TrustedProxyHeadersMiddleware` descarta `X-Forwarded-*`, `Forwarded` y `X-Real-IP` si la conexión no viene de `TRUSTED_PROXY_CIDRS`; DRF toma la IP de `X-Forwarded-For` con `NUM_PROXIES` (`API_NUM_PROXIES`). Caddy sobrescribe esas cabeceras que mande el cliente. Por defecto se confía en rangos privados; en producción conviene acotarlo a la subred de Docker.
- **Cambio forzado de contraseña**: permiso por defecto de DRF; mientras `debe_cambiar_contrasena` sea verdadero todo da 403 `{"codigo": "debe_cambiar_contrasena"}` salvo `me`, `logout` y `cambiar-contrasena`. Supuestos míos: la contraseña nueva debe ser distinta de la actual (si no, el cambio forzado no serviría), y las contraseñas se limitan a 256 caracteres para no mandar entradas enormes a Argon2. El cambio invalida las demás sesiones (cambia el hash de sesión) y se limita a 5 intentos por minuto por usuario.
- `logout` siempre responde 204 (idempotente, también sin sesión).
- Las sesiones vencidas no se borran solas de `django_session`: falta programar `manage.py clearsessions` (candidato: `proceso_diario`, sprint 4).
- Los puntos donde el commit 6 escribirá eventos están marcados con `# HOOK evento:` en `apps/accounts/services.py`.
- Migraciones y pruebas con el rol `migrator`; el servidor con `app`.
- En prod, `SECURE_SSL_REDIRECT` exime `/api/v1/health/` para que el healthcheck interno (HTTP) no reciba 301.

## Valores provisionales para confirmar
| Valor | Provisional | Variable |
| --- | --- | --- |
| Longitud mínima de contraseña | 10 | `PASSWORD_MIN_LENGTH` |
| Intentos fallidos antes de bloquear | 5 | `LOGIN_MAX_FAILED_ATTEMPTS` |
| Duración del bloqueo | 15 min | `LOGIN_LOCKOUT_MINUTES` |
| Límite de login por IP + correo | 5 por minuto | `LOGIN_RATE_PER_IP_EMAIL` |
| Límite de login por IP sola | 20 por minuto | `LOGIN_RATE_PER_IP` |
| Cierre de sesión por inactividad | 30 min | `SESSION_IDLE_MINUTES` |

## Riesgos aceptados
- **Bloqueo de cuenta por terceros**: cualquiera que conozca un correo válido puede provocar el bloqueo de esa cuenta durante 15 minutos con 5 intentos fallidos (denegación de servicio dirigida). Se mitiga con el límite de tasa por IP, que reduce la velocidad desde una sola IP, pero no lo impide si el atacante usa varias IPs. Alternativas si resulta un problema: bloqueo progresivo, bloquear por IP+cuenta en vez de solo por cuenta, o un desbloqueo por el administrador (no incluido en el sprint 1).

## Preguntas abiertas
- `numero_nomina`: DEFINICION §5 lo lista en Usuario pero no dice si es obligatorio. Queda **opcional**: `NULL` cuando no existe (nunca cadena vacía) y **único cuando tiene valor** (restricción parcial). Confirmar si debe ser obligatorio.

## Cambios que Vul debe hacer a docs/DEFINICION.md (archivo protegido)
- §5 (Usuario): agregar `debe_cambiar_contrasena` (booleano) y `creado_en`; precisar que `no. nomina` es `NULL` si no existe y único si existe.
- §6.9 (tipos de evento): agregar `USUARIO_CONTRASENA_CAMBIADA` (el usuario cambia su propia contraseña); no hay tipo para el cierre de sesión (decidir si se registra).
- §8 / §13.8: registrar los valores definitivos de la tabla de «Valores provisionales».
- §13.9: el restablecimiento por el administrador exige cambio de contraseña en el siguiente inicio de sesión.
