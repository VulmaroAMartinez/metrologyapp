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
- **Se mantiene `FOR NO KEY UPDATE NOWAIT`** (decisión de Vul tras comparar con «reservar el intento»: un `UPDATE … RETURNING` que incrementa `intentos_fallidos` antes de verificar). **Se evaluará reservar el intento en el sprint 6 si el evento `motivo = intento_simultaneo` muestra colisiones reales** (consultar la bitácora: `despues->>'motivo' = 'intento_simultaneo'`). Lo que habría que resolver al cambiar: pérdida de la atomicidad contador+evento en una transacción, devolver la reserva en un login correcto, marcar el bloqueo en el último intento permitido y los intentos contados por un worker que muere.
- **Límite de tasa**: ventana fija de 1 minuto sobre la caché en base de datos (`django_cache`, tabla creada por migración), una ranura por intento con `cache.add` (atómico entre workers; `cache.incr` no lo es). Se evalúa antes de calcular cualquier hash y responde 429 con `Retry-After`. Limitación conocida: una ventana fija permite hasta el doble del límite alrededor del cambio de minuto. Los identificadores se guardan hasheados.
- **IP real**: `TrustedProxyHeadersMiddleware` descarta `X-Forwarded-*`, `Forwarded` y `X-Real-IP` si la conexión no viene de `TRUSTED_PROXY_CIDRS`; DRF toma la IP de `X-Forwarded-For` con `NUM_PROXIES` (`API_NUM_PROXIES`). Caddy sobrescribe esas cabeceras que mande el cliente. Por defecto se confía en rangos privados; en producción conviene acotarlo a la subred de Docker.
- **`config.settings.prod` rechaza `TRUSTED_PROXY_CIDRS` amplio**: si conserva el valor por defecto (todos los rangos privados), está vacío, tiene un CIDR inválido o una red más ancha que /16 (IPv4) o /64 (IPv6). Cómo acotarlo a la subred de Docker: `docs/guia-instalacion.md`.
- **Permisos por rol** (commit 7, `apps/accounts/permissions.py`): enum `Accion` y una única tabla `ACCIONES_POR_ROL` que refleja §3 (lista explícita por rol: una acción nueva que no se agregue queda denegada para todos). Clases DRF: `requiere(Accion.X)` (sesión + sin cambio de contraseña pendiente + rol con la acción), `AllowAny` (público) e `IsAuthenticated` (cualquier rol; solo `me` y `cambiar-contrasena`). **Una vista que no declara permisos se deniega** (`DenegarPorDefecto`). `GENERAR_REPORTES` es una acción aparte aunque hoy coincida con `CONSULTAR` (§3 une los reportes a la consulta; §7 los da a los 3 roles); `RECIBIR_CORREOS_CADUCIDAD` está en la tabla porque §3 la lista, pero no la usa ningún endpoint (la usará el proceso diario).
- **Política de respuesta, igual para todos los recursos**: sin sesión → 401 `no_autenticado`; con sesión pero el rol no tiene la acción → **403** `permiso_denegado` (nunca 404 para ocultar recursos); cambio de contraseña pendiente → 403 `debe_cambiar_contrasena`; CSRF inválido → 403 `csrf_invalido`. Todos los errores de la API tienen la forma `{"codigo": ..., "detalle": ...}` (validación: `datos_invalidos`, 405: `metodo_no_permitido`, etc.; `apps/core/exceptions.py`).
- **Las denegaciones de permiso no generan evento** (§6.9 no define el tipo): solo una línea de log (`Permiso denegado: usuario_id=… rol=… accion=… metodo=… ruta=…`), sin correo, sesión ni token.
- **Cada sprint debe registrar sus endpoints** en `REGISTRO` (`apps/accounts/tests/test_permission_coverage.py`): una prueba recorre todas las rutas y falla si una vista no declara permisos, si una ruta no está registrada o si sus métodos o su política no coinciden con lo registrado. La vista `schema` no es DRF y está en `VISTAS_NO_DRF`.
- **Gestión de usuarios** (commit 8; `/api/v1/usuarios/`, solo ADMIN con la acción `GESTIONAR_USUARIOS`): listar (paginado con `pagina` y `tamano`, 25 por defecto y máximo 100; filtros `rol`, `activo`; búsqueda `buscar` en nombre, correo y número de nómina; `orden` solo por nombre, correo, rol, activo o creado_en, nunca por columnas sensibles), detalle, crear, editar (solo nombre, correo y numero_nomina), cambiar rol, desactivar, reactivar y restablecer contraseña. **No hay borrado físico ni desbloqueo manual** (`DELETE` responde 405). Los serializadores rechazan campos desconocidos: el administrador no puede mandar una `contrasena`, ni un `rol` dentro de una edición. Las respuestas muestran `id, nombre, correo, numero_nomina, rol, activo, debe_cambiar_contrasena, creado_en, bloqueado_hasta` (solo lectura); nunca el hash ni `intentos_fallidos`.
- **Contraseña temporal**: la genera el sistema con `secrets` (20 caracteres, mínimo 16; más si `PASSWORD_MIN_LENGTH` lo exige; mayúsculas, minúsculas y dígitos; pasa los validadores). Nunca la escribe el administrador. Se devuelve **una sola vez**, en la respuesta de crear y de restablecer, con `Cache-Control: no-store`; no se puede volver a consultar. Al crear o restablecer, `debe_cambiar_contrasena` queda en verdadero. No aparece en eventos, logs ni mensajes de error (pruebas tras cada operación). El primer administrador no usa la API: lo crea `manage.py crear_admin` (contraseña por prompt interactivo; `CREAR_ADMIN_CONTRASENA` solo para instalaciones automatizadas) y también nace con el cambio obligatorio.
- **Restablecer, reactivar y desactivar no tocan `intentos_fallidos` ni `bloqueado_hasta`** (sería un desbloqueo manual, descartado). Una cuenta bloqueada a la que se le restablece la contraseña sigue bloqueada hasta que venza (prueba con reloj controlado).
- **Reglas de administradores**: un administrador no puede desactivarse, cambiarse el rol ni **restablecerse la contraseña** a sí mismo desde la administración (403 `auto_modificacion_no_permitida`; decisión de Vul): la vía propia es `cambiar-contrasena`, que pide la contraseña actual. Sí puede editar sus propios datos. Cada servicio bloquea (`FOR NO KEY UPDATE`, en orden de pk) todas las filas de administradores activos dentro de su transacción y **revalida al actor** con ese estado: dos administradores que operan a la vez se serializan y el sistema nunca queda sin un administrador activo (si el actor dejó de serlo mientras se procesaba: 403 `permiso_denegado`; la comprobación de «último administrador» → 409 `ultimo_administrador` queda como defensa adicional). Pruebas con hilos: dos administradores que se degradan o desactivan mutuamente, ciclo de tres, y dos envíos simultáneos del mismo formulario; sin el candado fallan.
- **Sesiones**: al **desactivar** o **restablecer la contraseña** se borran de `django_session` las sesiones de ese usuario en la misma transacción (y quedarían inválidas de todos modos: usuario inactivo / hash de sesión distinto). **Al cambiar el rol la sesión sigue abierta** y el rol se lee de la base en cada petición, así que el cambio es inmediato: los permisos nuevos rigen desde la siguiente petición (decisión de Vul confirmada; ni se cierra la sesión ni se borran sus filas). `django_session` no tiene columna de usuario: se decodifica cada sesión vigente (O(sesiones), aceptable para este volumen).
- **Normalización**: correo en minúsculas y sin espacios; `numero_nomina` sin espacios en los extremos y vacío → `NULL`; nombre obligatorio, máximo 150. Correo y `numero_nomina` son únicos **sin distinguir mayúsculas** (índices sobre `Lower(...)`; migración 0003 de accounts). Duplicados → 400 `correo_duplicado` / `numero_nomina_duplicado`, incluso con dos envíos simultáneos (se captura `IntegrityError` por nombre de restricción).
- **Eventos de usuarios**: `USUARIO_CREADO`, `USUARIO_EDITADO` (nuevo), `USUARIO_ROL_CAMBIADO`, `USUARIO_DESACTIVADO`, `USUARIO_REACTIVADO` (nuevo) y `USUARIO_CONTRASENA_RESTABLECIDA`. El campo `usuario` del evento es quien actúa; el afectado se identifica en la descripción (`correo (id N)`), porque `Evento` no tiene otra FK a usuario. Una operación sin cambio real (mismo rol, ya inactivo, edición idéntica…) no escribe ni evento. Se pidieron «tres tipos nuevos» pero solo se nombraron dos (`USUARIO_EDITADO`, `USUARIO_REACTIVADO`); se agregaron esos dos.
- **Cambio forzado de contraseña**: permiso por defecto de DRF; mientras `debe_cambiar_contrasena` sea verdadero todo da 403 `{"codigo": "debe_cambiar_contrasena"}` salvo `me`, `logout` y `cambiar-contrasena`. Supuestos míos: la contraseña nueva debe ser distinta de la actual (si no, el cambio forzado no serviría), y las contraseñas se limitan a 256 caracteres para no mandar entradas enormes a Argon2. El cambio invalida las demás sesiones (cambia el hash de sesión) y se limita a 5 intentos por minuto por usuario.
- `logout` siempre responde 204 (idempotente, también sin sesión).
- Las sesiones vencidas no se borran solas de `django_session`: falta programar `manage.py clearsessions` (candidato: `proceso_diario`, sprint 4).
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

Lista consolidada del sprint 1; `DEFINICION.md` no se editó.

**§3 Usuarios y permisos**
- «Reportes» está dentro de la primera fila (consulta) pero §7 los trata como una acción propia para los 3 roles: separar `GENERAR_REPORTES` de `CONSULTAR` aunque hoy coincidan.
- «Recibir correos de caducidad» es un atributo del rol (lo usará el proceso diario), no un permiso de API.
- «Gestionar usuarios»: precisar las reglas: nadie puede desactivarse, cambiar su propio rol ni restablecer su propia contraseña desde la administración (su vía es cambiar-contrasena); siempre debe quedar al menos un administrador activo; no hay borrado físico; **no hay desbloqueo manual** de cuentas (solo vence `bloqueado_hasta`); el administrador no escribe contraseñas: el sistema genera una temporal que se muestra una sola vez.
- Los permisos se verifican siempre en el backend, con 401 (sin sesión) y 403 uniforme (rol sin la acción, nunca 404).

**§4 Stack y arquitectura**
- Fijar versiones: PostgreSQL 18, Python 3.14, Django 5.2 LTS, Node 24 LTS, Caddy 2.11; sesión por cookie confirmada (hoy [PROPUESTA]).
- Dos roles de base de datos: `migrator` (dueño del esquema; migraciones y pruebas) y `app` (ejecución, sin DDL).

**§5 Modelo de datos**
- Usuario: agregar `debe_cambiar_contrasena` (booleano), `creado_en`; `no. nomina` es `NULL` si no existe y único (sin distinguir mayúsculas) si existe; correo único sin distinguir mayúsculas y guardado en minúsculas; sin `is_staff`/`is_superuser`.
- Evento: `usuario` nulo = Sistema (FK protegida); agregar `ip` (nulo) y `instrumento_id` (entero nulo hasta que exista la FK en el sprint 2); `descripcion` va sin fecha (el formato `dd-mm-aaaa hh:mm · ID · texto · usuario` se arma al mostrar); `antes`/`despues` solo aceptan una lista blanca de campos (nunca contraseñas, hashes ni contadores).

**§6.9 Eventos**
- Agregar los tipos `CUENTA_BLOQUEADA`, `USUARIO_CONTRASENA_CAMBIADA` (el usuario cambia su propia contraseña), `USUARIO_EDITADO` y `USUARIO_REACTIVADO`.
- No generan evento: el cierre de sesión, las denegaciones de permiso y los rechazos por límite de tasa (429); solo una línea de log sin datos sensibles.
- `SESION_FALLIDA`: exactamente un evento por intento fallido; no guarda el correo escrito (para un correo inexistente el evento queda sin usuario); guarda IP y motivo interno. El intento que bloquea la cuenta escribe además `CUENTA_BLOQUEADA`.
- Los eventos de gestión de usuarios identifican al afectado en la descripción (`correo (id N)`).
- «La tabla es solo de inserción»: el rol `app` no tiene UPDATE, DELETE ni TRUNCATE; el dueño (`migrator`) sí puede, y no debe usarse en ejecución.

**§8 Seguridad**
- Registrar los valores definitivos de la tabla «Valores provisionales para confirmar» (contraseña mínima, bloqueo, límites de tasa, inactividad).
- Argon2 con los parámetros por defecto de Django (≈100 MiB por verificación); cookies `HttpOnly`, `SameSite=Lax`, `Secure` y prefijo `__Host-` en producción; CSRF en todo POST (también el anónimo); CORS cerrado.
- El sistema solo se instala en **red interna o VPN**, nunca expuesto directo a internet.
- `TRUSTED_PROXY_CIDRS` debe acotarse a la subred de Docker (producción se niega a arrancar con un valor amplio).

**§9 Despliegue y operación**
- El primer administrador se crea con `manage.py crear_admin` (contraseña interactiva; variable de entorno solo en instalaciones automatizadas) y debe cambiarla al entrar. Ver `docs/guia-instalacion.md`.
- Programar `clearsessions` (candidato: el proceso diario del sprint 4).

**§13 Decisiones abiertas**
- 8 (política de contraseñas, bloqueo, inactividad): quedan los valores provisionales.
- 9 (restablecer contraseña): resuelto — lo hace el administrador; el sistema genera una temporal que se muestra una vez y obliga a cambiarla.
- 10 (idioma de los identificadores): resuelto — dominio en español como en la definición; código técnico en inglés.
