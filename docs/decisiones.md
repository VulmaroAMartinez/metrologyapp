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
- **Eventos** (commit 6, `apps/audit`): `Evento` es solo de inserción en dos capas. En el código: `save()` sobre un registro existente, `delete()` y, en el queryset, `update`, `delete`, `bulk_update` y `bulk_create(update_conflicts=True)` lanzan `EventoInmutableError`. En la base: la migración revoca `UPDATE, DELETE, TRUNCATE` al rol `app` (los privilegios por defecto se los darían) y una prueba lo comprueba con el rol `app` sobre la base real. **Límite**: el rol `migrator` (dueño de la tabla) sí puede modificarla; se usa solo para migraciones y pruebas y no debe usarse en ejecución.
- `registrar_evento()` lanza error fuera de `transaction.atomic()`: el cambio y su evento se confirman o se revierten juntos. El login escribe contador, sesión y evento en una misma transacción, de modo que un fallo (401) **sí** confirma contador y evento; si el evento falla, todo se revierte (500).
- **Saneamiento**: `antes`/`despues` solo aceptan la lista blanca de `apps/audit/sanitize.py`, con valores escalares y sin forma de hash; cualquier otra clave (contraseñas, hashes, tokens, `intentos_fallidos`…) lanza error. Cada sprint que audite datos nuevos debe ampliar la lista.
- **SESION_FALLIDA**: exactamente una inserción por intento fallido, exista o no el usuario. Para un correo inexistente el evento queda sin usuario y **no guarda el correo** (ni en la descripción ni en el JSON); sí guarda la IP y el motivo interno (`despues = {"motivo": ...}`). El intento que además bloquea la cuenta escribe dos eventos (`SESION_FALLIDA` + `CUENTA_BLOQUEADA`).
- **Los 429 no generan evento** (para no llenar la tabla bajo ataque): solo una línea en el log de la aplicación (`Límite de tasa excedido; reintento en N s`), sin correo, IP ni contraseña.
- El cierre de sesión no genera evento (§6.9 no define tipo).
- La lista de tipos queda fija en una `CheckConstraint`: agregar un tipo en un sprint posterior requiere una migración.
- Las pruebas de roles (privilegios de `app` y tabla de eventos) usan una base propia, `metrologia_roles_test`: la crea y la destruye la sesión de pruebas como `migrator`, le aplica el mismo `deploy/db/privileges.sql` que `init.sh` usa para la base real y la migra. `make test` no migra ni escribe en la base de desarrollo (comprobado: secuencia de eventos, filas y migraciones idénticas antes y después). `init.sql` quedó solo con los roles y el dueño de la base; los privilegios por base están en `privileges.sql`.
- Los datos sensibles no entran a logs: `django.db.backends` fijado en INFO (a nivel DEBUG registraría el hash en el SQL).
- **Autenticación** (commit 5): sesión en base de datos (cerrar sesión la borra en el servidor), ventana deslizante de inactividad, clave de sesión rotada al iniciar sesión. Cookies `HttpOnly`, `SameSite=Lax`; en prod `Secure` y prefijo `__Host-` (sin Domain, Path=/). La cookie CSRF también es `HttpOnly`: el token se obtiene de `GET /api/v1/auth/csrf/` y **rota al iniciar sesión** (el cliente debe pedirlo de nuevo). CSRF se exige en todo POST, también anónimo (login incluido). CORS cerrado: no se instala `django-cors-headers`; mismo origen vía Caddy.
- **Login indistinguible**: correo inexistente, contraseña incorrecta, usuario inactivo, cuenta bloqueada y usuario sin contraseña devuelven 401 con el mismo cuerpo y cabeceras, y siempre ejecutan una verificación de hash (contra un hash ficticio precalculado cuando no hay usuario elegible). El motivo real solo va a logs internos (sin correo ni contraseña).
- **Contador de fallos**: un único `UPDATE` atómico (sin leer-modificar-guardar). Al llegar al límite se fija `bloqueado_hasta` y el contador queda en el límite mientras dure el bloqueo; los intentos durante el bloqueo no lo alargan; al vencer, el siguiente fallo abre un ciclo nuevo (cuenta 1) y un login correcto lo reinicia a 0.
- **Intentos simultáneos contra una misma cuenta**: el login lee la fila con `SELECT ... FOR NO KEY UPDATE NOWAIT` y la mantiene bloqueada hasta registrar el resultado. Sin candado, una ráfaga en paralelo pasaba la comprobación de «no bloqueado» antes de registrarse ningún fallo y superaba el umbral. `NOWAIT` evita que alguien sature los workers esperando el candado de una cuenta; quien no lo obtiene recibe el mismo 401 genérico tras una verificación de hash ficticia (mismo costo, para no delatar que la cuenta existe). `NO KEY UPDATE` y no `UPDATE` porque el evento del intento rechazado inserta una fila con llave foránea al usuario (`FOR KEY SHARE`) y con `FOR UPDATE` esa inserción esperaba al otro intento (defecto hallado con una prueba de dos logins reales; sin el cambio esperaba 1 s). Solo se trata como intento simultáneo el error SQLSTATE `55P03` (candado no disponible); cualquier otro error de base de datos se propaga. Consecuencia aceptada: un intento legítimo que coincida exactamente con otro contra la misma cuenta puede recibir 401 y debe reintentar; el límite de tasa acota cuántos puede provocar un atacante. (Hallazgos de la revisión de seguridad automática del commit 5.)
- **Límite de tasa**: ventana fija de 1 minuto sobre la caché en base de datos (`django_cache`, tabla creada por migración), una ranura por intento con `cache.add` (atómico entre workers; `cache.incr` no lo es). Se evalúa antes de calcular cualquier hash y responde 429 con `Retry-After`. Limitación conocida: una ventana fija permite hasta el doble del límite alrededor del cambio de minuto. Los identificadores se guardan hasheados.
- **IP real**: `TrustedProxyHeadersMiddleware` descarta `X-Forwarded-*`, `Forwarded` y `X-Real-IP` si la conexión no viene de `TRUSTED_PROXY_CIDRS`; DRF toma la IP de `X-Forwarded-For` con `NUM_PROXIES` (`API_NUM_PROXIES`). Caddy sobrescribe esas cabeceras que mande el cliente. Por defecto se confía en rangos privados; en producción conviene acotarlo a la subred de Docker.
- **`config.settings.prod` rechaza `TRUSTED_PROXY_CIDRS` amplio**: si conserva el valor por defecto (todos los rangos privados), está vacío, tiene un CIDR inválido o una red más ancha que /16 (IPv4) o /64 (IPv6). Cómo acotarlo a la subred de Docker: `docs/guia-instalacion.md`.
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

## Decisiones de Vul (cierre del commit 5)
- **No hay desbloqueo manual de cuentas**: ni endpoint, ni comando `desbloquear_usuario`, ni evento `USUARIO_DESBLOQUEADO`. Una cuenta bloqueada solo se libera cuando vence `bloqueado_hasta`. **Consecuencia**: una cuenta que sea objetivo de ataques repetidos puede permanecer bloqueada de forma intermitente (el atacante la vuelve a bloquear al vencer). **Mitigaciones**: evento `CUENTA_BLOQUEADA` en la bitácora (permite detectarlo), límite de tasa por IP e instalación solo en red interna o VPN.
- **El bloqueo es por cuenta** (no por IP+cuenta).
- **Instalación solo en red interna o VPN**, nunca expuesta directo a internet. La guía de instalación (sprint 6) debe decirlo; el límite de tasa y el bloqueo asumen un atacante con pocas IPs.
- **Frontend (commit 11)**: el botón de login se deshabilita mientras se envía; el mensaje de error de login invita a reintentar (un intento duplicado puede recibir 401 por el `NOWAIT`); el token CSRF se obtiene de `/api/v1/auth/csrf/` y se envía en la cabecera `X-CSRFToken` (y se vuelve a pedir tras iniciar sesión, porque rota).

## Riesgos aceptados
- **Bloqueo de cuenta por terceros**: cualquiera que conozca un correo válido puede provocar el bloqueo de esa cuenta durante 15 minutos con 5 intentos fallidos (denegación de servicio dirigida). Se mitiga con el límite de tasa por IP, que reduce la velocidad desde una sola IP, pero no lo impide si el atacante usa varias IPs. Alternativas si resulta un problema: bloqueo progresivo, bloquear por IP+cuenta en vez de solo por cuenta, o un desbloqueo por el administrador (no incluido en el sprint 1).

## Preguntas abiertas
- `numero_nomina`: DEFINICION §5 lo lista en Usuario pero no dice si es obligatorio. Queda **opcional**: `NULL` cuando no existe (nunca cadena vacía) y **único cuando tiene valor** (restricción parcial). Confirmar si debe ser obligatorio.

## Cambios que Vul debe hacer a docs/DEFINICION.md (archivo protegido)
- §5 (Usuario): agregar `debe_cambiar_contrasena` (booleano) y `creado_en`; precisar que `no. nomina` es `NULL` si no existe y único si existe.
- §6.9 (tipos de evento): agregar `USUARIO_CONTRASENA_CAMBIADA` (el usuario cambia su propia contraseña) y `CUENTA_BLOQUEADA` (bloqueo por intentos fallidos); no hay tipo para el cierre de sesión (hoy no se registra). Aclarar que `SESION_FALLIDA` no guarda el correo escrito y que los rechazos por límite de tasa no generan evento.
- §3 / §6.9: el bloqueo de cuentas no tiene desbloqueo manual (solo vence `bloqueado_hasta`) y el sistema solo se instala en red interna o VPN.
- §8 / §13.8: registrar los valores definitivos de la tabla de «Valores provisionales».
- §13.9: el restablecimiento por el administrador exige cambio de contraseña en el siguiente inicio de sesión.
