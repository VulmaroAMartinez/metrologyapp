# Modelo de amenazas — sprint 1

Alcance: lo construido en el sprint 1 (cuentas, sesiones, permisos, bitácora, proxy, base de datos,
contenedores, integración continua). Lo que **no** existe todavía (catálogos, instrumentos, certificados PDF,
correo, frontend real) aparece solo como activo futuro o en «No cubierto». Cada control cita su evidencia
(pruebas en `backend/`, `make` o scripts de `deploy/`); `docs/seguridad-checklist.md` tiene el detalle por control.

Premisas de la definición (DEFINICION v1.1 §1, §8, §9): una instalación por empresa, **solo red interna o VPN**,
dos administradores por instalación, servidor Linux propio.

## 1. Activos

| Activo | Qué es | Por qué importa | Dónde vive |
| --- | --- | --- | --- |
| Cuentas | Correo, rol, estado, hash Argon2, contadores de bloqueo | Quien controla una cuenta ADMIN controla el sistema | Tabla `accounts_usuario` |
| Sesiones | Cookie de sesión y su fila en base de datos | Equivale a la identidad mientras dura | `django_session`, cookie `HttpOnly` |
| Bitácora | Eventos de auditoría (solo inserción) | Única evidencia de quién hizo qué | Tabla `audit_evento` |
| Certificados (futuro, sprint 2+) | PDF de calibración y su huella SHA-256 | Evidencia legal/de calidad de la calibración | Volumen de archivos (aún no existe) |
| Base de datos | Todos los datos de negocio y los roles `app` / `migrator` | Integridad y confidencialidad de todo | Contenedor `db`, volumen persistente |
| Secretos | `SECRET_KEY`, contraseñas de BD, clave de administrador inicial | Permiten falsificar sesiones o leer la base | Variables de entorno / `.env` del servidor |

## 2. Actores

| Actor | Capacidad supuesta | Objetivo típico |
| --- | --- | --- |
| Técnico malicioso | Cuenta TECNICO válida, acceso a la API | Escalar a ADMIN, ver la bitácora, cambiar roles, borrar rastros |
| Espectador | Cuenta de solo lectura | Escribir datos, ver lo que no le toca |
| Administrador comprometido | Sesión o contraseña de un ADMIN robada | Crear cuentas, restablecer contraseñas, ocultarse |
| Atacante en la red interna | Llega a Caddy; sin cuenta | Adivinar contraseñas, bloquear cuentas, falsificar cabeceras, escanear |
| Quien tiene acceso al servidor | Root en el host o miembro del grupo `docker` | Leer `.env`, la base, modificar la bitácora con `migrator` |
| Cadena de suministro | Paquete, imagen o acción de CI comprometidos | Ejecutar código en build/CI/producción |

## 3. Fronteras de confianza

```
Navegador ──(1)──> Caddy ──(2)──> Django (api) ──(3)──> PostgreSQL
                                      \
Host (Docker, .env, volúmenes) ──(4)── contenedores
```

| # | Frontera | Qué cruza | Quién debe confiar en qué |
| --- | --- | --- | --- |
| 1 | Navegador → Caddy | HTTP(S), cookies, cabeceras del cliente (no confiables) | Caddy no confía en nada del cliente |
| 2 | Caddy → Django | Petición reenviada con `X-Forwarded-*` fijados por Caddy | Django solo cree esas cabeceras si la conexión viene de `TRUSTED_PROXY_CIDRS` |
| 3 | Django → PostgreSQL | SQL con el rol `app` (sin DDL, sin UPDATE/DELETE en `audit_evento`) | La base no confía en la aplicación más allá de sus privilegios |
| 4 | Host → contenedores | Variables de entorno, volúmenes, redes Docker | Los contenedores no confían en el host de red (redes `internal`); el host sí es de confianza total |

## 4. Tabla STRIDE por componente

S = suplantación, T = manipulación, R = repudio, I = divulgación de información, D = denegación de servicio,
E = elevación de privilegios. «¿Se acepta?»: **Sí** = riesgo residual aceptado explícitamente; **No** = pendiente
de mitigar; **Parcial** = mitigado en parte y lo que queda está en «No cubierto».

### 4.1 Caddy (proxy, frontera 1 y 2)

| | Amenaza | Control existente | Evidencia | Riesgo residual | ¿Se acepta? |
| --- | --- | --- | --- | --- | --- |
| S | Cliente falsifica su IP con `X-Forwarded-For`, `X-Real-IP` o `Forwarded` para evadir el límite de tasa | Caddy sobrescribe las cabeceras; `TrustedProxyHeadersMiddleware` las descarta si la conexión no viene del proxy | `apps/core/tests/test_trusted_proxy.py`, `proxytests/test_proxy_headers.py` (stack real) | Un proxy mal configurado en producción; mitigado porque prod se niega a arrancar con `TRUSTED_PROXY_CIDRS` amplio | Sí |
| S | `X-Forwarded-Proto` falso para saltarse la redirección a HTTPS | Se descarta fuera del proxy de confianza | `proxytests/test_proxy_headers.py` | — | Sí |
| T | Cabeceras de seguridad ausentes (sniffing, clickjacking, caché) | `nosniff`, `Referrer-Policy`, `frame-ancestors`, `X-Frame-Options`, `no-store` en toda la API, también en errores | `config/tests/test_security_headers_prod.py`, `proxytests/test_cabeceras_cookies.py`, `apps/core/tests/test_no_store.py` | CSP del frontend: no existe aún | Parcial |
| I | Tráfico en claro | HSTS solo sobre HTTPS, corto (1 h); cookies `Secure` y `__Host-` en producción | `config/tests/test_prod_settings.py`, `proxytests/test_cabeceras_cookies.py` | En desarrollo Caddy sirve HTTP; **TLS de producción no está configurado** (sprint 6) | No |
| D | Ráfagas de peticiones | Límite de tasa por IP y por IP+correo antes de calcular hashes (429) | `apps/core/tests/test_ratelimit.py`, `proxytests/test_ventana_de_tasa.py` | Ventana fija: hasta 2× el límite en el cambio de minuto; muchos clientes tras una sola IP (NAT) se limitan entre sí | Sí |
| E | Caddy con salida a internet (exfiltración tras una intrusión) | Red `edge` sin NAT | `deploy/verificar_contenedores.sh` | En Docker Desktop el proxy **sí** alcanza internet (medido); falta regla `DOCKER-USER` | No (sprint 6) |

### 4.2 API Django — autenticación y sesiones

| | Amenaza | Control existente | Evidencia | Riesgo residual | ¿Se acepta? |
| --- | --- | --- | --- | --- | --- |
| S | Adivinar contraseñas (fuerza bruta, credential stuffing) | Argon2; validadores (mín. 10, comunes, numéricas, similitud); bloqueo tras 5 fallos (15 min); límite de tasa | `apps/accounts/tests/test_password_security.py`, `test_login_failures.py`, `apps/core/tests/test_ratelimit.py` | Sin MFA; atacante con muchas IPs | Sí |
| S | Enumeración de cuentas (correo existente o no) | Misma respuesta 401 y cabeceras en todo fallo; siempre una verificación de hash (hash ficticio) | `apps/accounts/tests/test_login_failures.py` | Diferencias de tiempo medibles con red muy limpia: el costo es igual por diseño, no probado estadísticamente | Sí |
| S | Fijación y robo de sesión | Clave rotada al iniciar sesión; cookie `HttpOnly`, `SameSite=Lax`, `Secure`/`__Host-` en prod; vida máxima absoluta 12 h; inactividad 30 min | `apps/accounts/tests/test_session_cookies.py`, `test_session_absolute.py`, `test_auth_flow.py` | Una cookie robada sirve hasta que expira (sin vinculación a dispositivo) | Sí |
| S | Sesión vieja que revive tras reactivar o restablecer | `sesion_version` en el hash de sesión (el borrado de filas es solo limpieza) | `apps/accounts/tests/test_session_version.py`, `test_sessions_after_commit.py` | Al desplegar el cambio se cierran las sesiones existentes una vez | Sí |
| T | CSRF contra acciones autenticadas | Token obligatorio en todo POST (también el anónimo); CORS cerrado, mismo origen | `apps/accounts/tests/test_session_cookies.py` | — | Sí |
| R | Negar un intento de acceso | `SESION_INICIADA`, `SESION_FALLIDA` (una por intento) y `CUENTA_BLOQUEADA` en la misma transacción | `apps/accounts/tests/test_auth_events.py` | El cierre de sesión y los 429 **no** generan evento (decisión de Vul) | Sí |
| I | Contraseñas o hashes en respuestas, logs, eventos o errores | La contraseña nunca se devuelve ni se registra; `sanitize` rechaza claves sensibles; `django.db.backends` en INFO | `apps/accounts/tests/test_usuarios_api_seguridad.py`, `apps/audit/tests/test_registrar_evento.py` | Volcado de memoria / `DEBUG` mal puesto: prod se niega con configuraciones inseguras, no cubre el host | Sí |
| D | Bloquear a un tercero a propósito (5 intentos) | Límite por IP; evento `CUENTA_BLOQUEADA`; sin desbloqueo manual **por decisión** | `apps/accounts/tests/test_login_failures.py` | Cuenta atacada puede quedar bloqueada de forma intermitente | Sí (decisión de Vul) |
| D | Agotar memoria con Argon2 (~100 MiB por verificación) | Límite de tasa antes del hash; `mem_limit: 768m`, contraseñas ≤ 256 caracteres | `deploy/verificar_contenedores.sh`, `apps/accounts/tests/test_password_change.py` | Ráfagas de IPs distintas pueden reiniciar el contenedor | Parcial |
| D | Intentos simultáneos contra una cuenta saturan workers | `FOR NO KEY UPDATE NOWAIT`: quien no obtiene el candado no espera | `apps/accounts/tests/test_login_lock_errors.py`, `test_admin_concurrency.py` | Un intento legítimo coincidente recibe 401 y reintenta | Sí |

### 4.3 API Django — permisos y gestión de usuarios

| | Amenaza | Control existente | Evidencia | Riesgo residual | ¿Se acepta? |
| --- | --- | --- | --- | --- | --- |
| E | Técnico o espectador ejecuta acciones de ADMIN | Matriz rol → acciones; toda vista declara permiso, la que no, se deniega; 403 uniforme | `apps/accounts/tests/test_permission_matrix.py`, `test_permission_coverage.py` | Una vista nueva mal registrada: la prueba de recorrido de rutas lo detecta en CI | Sí |
| E | Un administrador se desactiva o se degrada y deja el sistema sin administrador, o dos lo hacen a la vez | Reglas de auto-modificación; candado L0 + fila del objetivo; `ultimo_administrador` | `apps/accounts/tests/test_usuarios_concurrencia.py`, `test_admin_concurrency.py`, `test_admin_lock_rule.py` (ast) | — | Sí |
| E | Mass assignment (cambiar `rol` o `activo` desde una edición) | Serializadores rechazan campos desconocidos; `limpiar_campos` solo deja nombre, correo y nómina | `apps/accounts/tests/test_usuarios_api_crear_editar.py`, `test_admin_lock_rule.py` | — | Sí |
| T | Carreras que dejan estado a medias | Cada servicio es atómico con su evento; 409/503 sin estado parcial | `apps/accounts/tests/test_admin_concurrency.py` | — | Sí |
| R | Un administrador niega haber restablecido una contraseña | `USUARIO_CONTRASENA_RESTABLECIDA` con actor y afectado | `apps/accounts/tests/test_usuarios_servicios_eventos.py` | El administrador **conoce** las contraseñas que asigna y puede usarlas sin dejar evento de “uso” | Sí (decisión del equipo) |
| E | Administrador comprometido crea cuentas o restablece otras | Eventos en la bitácora; no puede tocar la suya; no hay desbloqueo manual | `apps/accounts/tests/test_usuarios_api_acciones.py` | **Sin MFA ni aprobación de dos personas**: un ADMIN comprometido tiene control total hasta que el otro lo detecte | Parcial |

### 4.4 Bitácora (`audit_evento`)

| | Amenaza | Control existente | Evidencia | Riesgo residual | ¿Se acepta? |
| --- | --- | --- | --- | --- | --- |
| T | Modificar o borrar eventos | Solo inserción en código (`save`, `delete`, `update`, `bulk_*` lanzan error) y en base (el rol `app` sin UPDATE/DELETE/TRUNCATE) | `apps/audit/tests/test_evento_model.py`, `test_real_db_roles.py` | El dueño `migrator` y quien tenga acceso al servidor sí pueden editarla; **no hay cadena de hashes ni copia externa** | Parcial |
| T | Inyectar datos sensibles en `antes`/`despues` | Lista blanca de campos y valores escalares, también al leer | `apps/audit/tests/test_registrar_evento.py`, `test_bitacora_api.py` | La lista es única, no por tipo de evento (sprint 2) | Sí |
| R | Acción sin evento | `registrar_evento()` falla fuera de una transacción; el evento va en la misma transacción que el cambio | `apps/audit/tests/test_registrar_evento.py` | Lecturas y descargas **no** se registran (decisión) | Sí |
| I | Un no-ADMIN lee la bitácora | `VER_BITACORA` solo ADMIN (403 para los demás); la consulta trae del usuario solo `id` y `nombre` | `apps/audit/tests/test_bitacora_api.py` | — | Sí |
| D | Consultas lentas por tamaño | Paginación por cursor sin COUNT/OFFSET; índice `(ocurrido_en, id)` | `apps/audit/tests/test_bitacora_rendimiento.py` | Crecimiento de la tabla sin purga (no se puede borrar) | Parcial |

### 4.5 PostgreSQL (frontera 3)

| | Amenaza | Control existente | Evidencia | Riesgo residual | ¿Se acepta? |
| --- | --- | --- | --- | --- | --- |
| E | Inyección SQL o compromiso de la API escala a DDL o a borrar la bitácora | Rol `app` sin DDL; ORM con consultas parametrizadas; privilegios por base | `apps/core/tests/test_db_roles.py`, `apps/audit/tests/test_real_db_roles.py` | Una API comprometida aún puede leer/escribir datos de negocio con `app` | Sí |
| S | Acceso a la base desde fuera | Red `internal: true`, sin puertos publicados hacia el host | `config/tests/test_deploy_files.py`, `deploy/verificar_contenedores.sh` | — | Sí |
| T | Esquema alterado sin dejar rastro | Migraciones con rol `migrator`, probadas con datos reales | `apps/core/tests/test_migrations_with_data.py` | `migrator` no se usa en ejecución, pero existe | Sí |
| I | Contraseñas de BD débiles o de ejemplo en producción | `validate_production` rechaza `DB_PASSWORD` vacío y de ejemplo | `config/tests/test_prod_settings.py` | Solo se valida la contraseña de `app` | Parcial |

### 4.6 Host y contenedores (frontera 4)

| | Amenaza | Control existente | Evidencia | Riesgo residual | ¿Se acepta? |
| --- | --- | --- | --- | --- | --- |
| E | Escape o abuso desde un contenedor | Sin root, `cap_drop: ALL`, `no-new-privileges`, no privilegiado, sin socket de Docker, sistema de archivos de solo lectura, límites de memoria y pids | `deploy/verificar_contenedores.sh` (dentro de `make test-proxy`) | Kernel compartido | Sí |
| I | Secretos en el repositorio | Variables de entorno; `.env` ignorado; gitleaks en historial y archivos; hook que protege `.env*` | `make secrets`, `config/tests/test_hook_proteger_archivos.py` | El hook solo cubre `Edit|Write` (un `Bash` con `sed -i` no pasa por él) | Parcial |
| I | Quien tiene acceso al servidor lee `.env` y la base | — (fuera de alcance de la aplicación) | — | Control total del host = control total del sistema | Sí (premisa) |
| T | Cambiar la bitácora con acceso al servidor | `app` no puede; `migrator` y el superusuario de la base sí | — | Sin respaldo externo inmutable | **No cubierto** |

### 4.7 Cadena de suministro y CI

| | Amenaza | Control existente | Evidencia | Riesgo residual | ¿Se acepta? |
| --- | --- | --- | --- | --- | --- |
| T | Paquete Python alterado o malicioso | Dependencias con hashes (`--require-hashes`), solo wheels, imágenes con digest; `pip-audit` en `make ci` | `make audit`, `config/tests/test_deploy_files.py` | Un paquete legítimo comprometido antes de que exista un aviso | Parcial |
| T | Acción de GitHub comprometida | Acciones fijadas por SHA; token `contents: read`; sin secretos; Dependabot | `config/tests/test_github_files.py` | **El flujo no se ha ejecutado en GitHub todavía**; Dependabot propone SHA nuevos que alguien debe revisar | Parcial |
| E | PR malicioso que exfiltre secretos | El flujo no usa secretos ni `pull_request_target` | `config/tests/test_github_files.py` | — | Sí |
| T | Imagen base alterada | Tag exacto + digest `sha256` | `backend/Dockerfile`, `frontend/Dockerfile` | Sin firma de imágenes ni SBOM | Parcial |

## 5. NO cubierto en el sprint 1

Para no dar una falsa sensación de seguridad, esto **no** está resuelto (y dónde se retoma, si aplica):

1. **TLS en producción**: en desarrollo Caddy sirve HTTP; la configuración de producción con certificado y HSTS escalonado es del sprint 6.
2. **Frontend**: no hay Next.js real (solo un servidor de prueba): CSP, XSS en la interfaz, manejo del token CSRF en el cliente y `npm audit` no se han probado.
3. **Certificados PDF y subida de archivos** (sprint 2): validación de contenido, antivirus, ruta de almacenamiento, descarga autenticada y parser `multipart` limitado.
4. **Correo / SMTP** (sprint 4): inyección en cabeceras, filtrado de destinatarios, credenciales SMTP.
5. **MFA y aprobación de dos personas** para acciones de administrador: un ADMIN comprometido tiene control total.
6. **Inmutabilidad frente al dueño o al host**: sin cadena de hashes, firma ni copia externa de la bitácora.
7. **Respaldos y recuperación**: no existen aún (sprint 6); pérdida de datos o de la bitácora no está cubierta.
8. **Salida a internet del proxy** en Docker Desktop (medido) y regla `DOCKER-USER` en Linux (sin verificar).
9. **Lecturas y descargas sin auditar** (por decisión); tampoco cierres de sesión ni 429 (solo log).
10. **Denegación de servicio volumétrica** a nivel de red o de recursos más allá del límite por IP y `mem_limit`.
11. **Sesiones concurrentes** sin límite por usuario y sin vinculación de la cookie a un dispositivo.
12. **Rotación de secretos y de `SECRET_KEY`** (rotarla reinicia contadores de tasa; sin procedimiento documentado de rotación completa).
13. **Logs**: sin identificador de petición, rotación ni envío fuera del host (sprint 6).
14. **Cadena de suministro**: sin firma de imágenes, sin SBOM, sin revisión de licencias; el flujo de CI aún no corrió en GitHub.
15. **Deriva de reloj / zona horaria** del servidor (bloqueos, ventanas de tasa y vencimientos dependen de la hora).
16. **Hook de archivos protegidos**: hipótesis de evasión por alias de ruta sin probar; solo cubre `Edit|Write`.

## 6. Cómo mantener este documento
Cada sprint revisa las tablas de los componentes que toca y mueve lo que deje de estar «No cubierto» con su evidencia. Si una
fila cita una prueba que se renombra, actualizar aquí y en `docs/seguridad-checklist.md` (esta última tiene una prueba que lo verifica).
