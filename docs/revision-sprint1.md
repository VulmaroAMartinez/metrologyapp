# Revisión del sprint 1 — hallazgos y decisión tomada

Reconstruido desde `docs/decisiones.md` y el historial de git (commits `4667518`, `5dbf27f`, `2dc24e1`,
`51480a9` y los de la modo autónomo `67c4db1`…). Cada fila: qué se halló, qué se decidió y dónde está la
evidencia. «Vul» = decisión explícita de Vul; «Claude» = lo decidió Claude Code (pendiente de revisión).

## Revisión de seguridad y de código (commit `4667518`)

| # | Hallazgo | Decisión | Quién | Evidencia |
| --- | --- | --- | --- | --- |
| 1 | El hook `proteger-archivos.sh` tenía finales CRLF (bash lo rechazaba), `jq.exe` imprime `\r\n` y comparaba rutas solo con `/` | Normalizar `\`, mayúsculas, `.` y `..`; cubrir `.env*` y `secrets/`; fallar cerrado sin `jq` o sin ruta | Claude | `config/tests/test_hook_proteger_archivos.py` |
| 2 | `Evento.save()` sobre un pk asignado intentaba un UPDATE | `force_insert` y `base_manager_name = "objects"` (`audit.0005`) | Claude | `apps/audit/tests/test_evento_model.py`, `apps/audit/tests/test_real_db_roles.py` |
| 3 | `validate_production` aceptaba `ALLOWED_HOSTS=*`, `DB_PASSWORD` vacío y proxies no privados | Rechazarlos; lista de CIDR por defecto solo en `guards.py` | Claude | `config/tests/test_prod_settings.py` |
| 4 | Identificador del límite de tasa con SHA-256 sin clave (reversible con diccionario) | HMAC-SHA256 con `SECRET_KEY` | Claude | `apps/core/tests/test_ratelimit.py` |
| 5 | HSTS de un año por defecto: un error de HTTPS tardaba un año en corregirse | `HSTS_SECONDS=3600`, sin subdominios ni preload | Claude | `config/tests/test_security_headers_prod.py` |
| 6 | DRF aceptaba formularios y multipart sin que nada los usara | Parsers solo JSON (415 `tipo_no_soportado`) | Claude | `apps/core/tests/test_parsers.py` |
| 7 | La sesión podía durar indefinidamente con actividad | Vida máxima absoluta de 12 h (`SESSION_ABSOLUTE_HOURS`) | **Vul** | `apps/accounts/tests/test_session_absolute.py` |
| 8 | `.lower()` de Python y `LOWER()` de PostgreSQL difieren fuera de ASCII (rompía la unicidad sin distinguir mayúsculas) | Correo y `numero_nomina` solo ASCII | **Vul** | `apps/accounts/tests/test_usuario_model.py`, `accounts.0005` |
| 9 | La base de datos y el resto de servicios tenían salida a internet | Redes `internal`/`egress`/`edge`; solo `api` con salida | **Vul** | `config/tests/test_deploy_files.py`, `deploy/verificar_contenedores.sh` |
| 10 | `.proxytest.env` sin ignorar y clave del administrador en la línea de comandos | `.gitignore` y paso por entorno (`-e NOMBRE`) | Claude | `config/tests/test_deploy_files.py` |
| 11 | Git Bash convertía rutas y creaba la carpeta basura `backend;C` | `MSYS_NO_PATHCONV=1` en el Makefile | Claude | `config/tests/test_deploy_files.py` |

## El login chocaba con la administración (commit `5dbf27f`)

| # | Hallazgo | Decisión | Quién | Evidencia |
| --- | --- | --- | --- | --- |
| 12 | Cada operación bloqueaba todas las filas de administradores y calculaba Argon2 con ellas bloqueadas; el login de otro administrador recibía el 401 genérico (ventana 66–75 ms) | Candado consultivo L0 + fila del objetivo; hash fuera de la transacción; `lock_timeout` → 503 `reintentar` | Vul (diseño aprobado) | `apps/accounts/tests/test_admin_concurrency.py` (sin `sleep`), ventana medida 4,2 / 12 ms |
| 13 | Una contraseña podía pisar un cambio ajeno entre verificarla y aplicarla | 409 `contrasena_modificada` | Vul (diseño aprobado) | `test_admin_concurrency.py` |
| 14 | Riesgo de que otra función escriba `rol`/`activo` sin L0 | Regla escrita en el modelo y comprobada con `ast` | Vul (diseño aprobado) | `apps/accounts/tests/test_admin_lock_rule.py` |

## Errores y validaciones unificados (commit `2dc24e1`)

| # | Hallazgo | Decisión | Quién | Evidencia |
| --- | --- | --- | --- | --- |
| 15 | Errores de campo con dos formas distintas (`detalle` dict vs texto) y vistas que capturaban errores de negocio | `ErrorDeUsuario` es `APIException`; sobre `{codigo, detalle[, campos]}` en todo `datos_invalidos` | Vul (diseño aprobado) | `apps/accounts/tests/test_error_unification.py` |
| 16 | `cambiar-contrasena` reutilizaba el límite de login | `PASSWORD_CHANGE_RATE_PER_USER` | Vul (diseño aprobado) | `apps/accounts/tests/test_password_change.py` |
| 17 | `tamano` inválido de la bitácora se recortaba o usaba 25 en silencio | 400 `datos_invalidos` con `campos.tamano` | Vul (diseño aprobado) | `apps/audit/tests/test_bitacora_api.py` |
| 18 | ASCII solo validado en el servicio | `CHECK` en la base (`accounts.0005`) | Vul (diseño aprobado) | `apps/core/tests/test_migrations_with_data.py` |
| 19 | Sin contrato OpenAPI verificable | `@extend_schema` en todas las vistas; `make schema` en `make ci` | Vul (diseño aprobado) | `apps/core/tests/test_openapi_contract.py` |
| 20 | Migraciones sin probar con datos | Pruebas de `accounts.0002`–`0005` y `audit.0003` | Claude | `apps/core/tests/test_migrations_with_data.py` |

## Sesiones que reviven (commit `51480a9`)

| # | Hallazgo | Decisión | Quién | Evidencia |
| --- | --- | --- | --- | --- |
| 21 | Si el borrado de sesiones fallaba al desactivar, reactivar revivía la sesión anterior (fail-open) | `sesion_version` en el hash de sesión; el borrado es solo limpieza | **Vul** | `apps/accounts/tests/test_session_version.py` |

## Revisiones de la modo autónomo (fases 1–3)

| # | Hallazgo | Decisión | Quién | Evidencia |
| --- | --- | --- | --- | --- |
| 22 | Pruebas de proxy dependían del segundo del minuto (ventana fija de 429) | Alinear la ventana de 60 s; una prueba estática lo exige | Claude | `proxytests/ventana.py`, 10/10 corridas |
| 23 | Duplicados de fixtures y archivos de prueba de más de 600 líneas | Ayudantes compartidos, archivos por endpoint; 1199 pruebas antes y después | Claude | `docs/decisiones.md` §Fase 2 |
| 24 | `mock.patch` sobre internos de `services` (~40 sitios) | Paquete `services/` con puntos de inyección (`sustituir`) | Claude | `docs/decisiones.md` §Fase 2 |
| 25 | `.gitleaks.toml` seguía los nombres de archivo anteriores a la partición | Actualizar las rutas sin ampliar la exención | Claude | `make secrets` |
| 26 | Umbral de cobertura documentado en 85 % (real: 95 % con ramas) | README y checklist corregidos | Claude | `make test` |
| 27 | `docs/decisiones.md` con contradicciones internas y la lista «Cambios que Vul debe hacer» desactualizada frente a la v1.1 | Reconciliado; la lista pasa a «Pendiente para la v1.2» | Claude | `docs/decisiones.md` |

## Riesgos aceptados que siguen abiertos
Bloqueo de cuenta por terceros, `intento_simultaneo` con `NOWAIT`, proxy con salida en Docker Desktop,
el administrador conoce las contraseñas de los usuarios y el límite del hook (solo `Edit|Write`). Detalle en
`docs/decisiones.md` («Riesgos aceptados») y `docs/modelo-amenazas.md`.
