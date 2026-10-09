# Lista de verificación de seguridad — sprint 1

Cada control lleva su **evidencia**: el nombre de la prueba (`archivo.py::prueba`, relativo a `backend/`)
o el comando que lo demuestra. Una prueba (`config/tests/test_security_checklist.py`) comprueba que
todas las pruebas y todos los `make` citados aquí existen, así esta lista no se desactualiza en silencio.

Cómo reproducir todo: `make ci` (lint, auditoría de dependencias, pruebas, pruebas contra el stack
real y escaneo de secretos). Las pruebas marcadas «stack real» son de `make test-proxy`.

## 1. Contraseñas

| Control | Evidencia |
| --- | --- |
| Hash Argon2 (único hasher); en las pruebas se usa MD5 solo por velocidad y se verifica la configuración real | `apps/accounts/tests/test_password_security.py::test_real_configuration_uses_argon2_first`, `apps/accounts/tests/test_password_security.py::test_real_hasher_stores_argon2id_hash`, `apps/accounts/tests/test_password_security.py::test_prod_settings_use_argon2`, `apps/accounts/tests/test_password_security.py::test_test_settings_are_the_only_ones_with_a_fast_hasher` |
| Validadores: longitud mínima configurable (10), contraseñas comunes, solo numéricas, similitud con nombre y correo | `apps/accounts/tests/test_password_security.py::test_minimum_length_default_is_10`, `apps/accounts/tests/test_password_security.py::test_minimum_length_is_configurable_by_environment`, `apps/accounts/tests/test_password_security.py::test_common_passwords_are_rejected`, `apps/accounts/tests/test_password_security.py::test_numeric_only_passwords_are_rejected`, `apps/accounts/tests/test_password_security.py::test_passwords_similar_to_name_or_email_are_rejected` |
| Solo el administrador asigna y restablece contraseñas; la escribe él y pasa por los validadores | `apps/accounts/tests/test_usuarios_api.py::test_create_runs_the_password_through_the_validators`, `apps/accounts/tests/test_usuarios_api.py::test_reset_requires_the_password_and_runs_the_validators` |
| Técnicos y espectadores no pueden cambiar su contraseña por ninguna vía de la API | `apps/accounts/tests/test_password_change.py::test_technicians_and_spectators_cannot_change_their_password`, `proxytests/test_flujo_usuarios.py::test_the_admin_creates_a_technician_who_cannot_change_their_password` (stack real) |
| La contraseña nunca se devuelve, ni se registra en eventos, logs, errores ni trazas | `apps/accounts/tests/test_usuarios_api.py::test_create_stores_the_password_the_admin_wrote_and_never_returns_it`, `apps/accounts/tests/test_usuarios_api.py::test_passwords_never_reach_responses_events_or_logs_after_each_operation`, `apps/accounts/tests/test_usuarios_api.py::test_a_failure_in_the_middle_never_leaks_the_password_to_the_client_or_the_logs`, `apps/accounts/tests/test_usuarios_services.py::test_no_password_or_hash_reaches_events_or_logs`, `apps/accounts/tests/test_password_security.py::test_password_and_hash_never_reach_the_logs`, `apps/accounts/tests/test_restablecer_admin.py::test_the_password_never_reaches_events_or_logs` |
| Ninguna respuesta contiene un hash (recorre las respuestas con Argon2 real) | `apps/accounts/tests/test_usuarios_api.py::test_no_response_ever_contains_a_password_hash`, `apps/audit/tests/test_bitacora_api.py::test_real_flows_never_leak_hashes_passwords_or_foreign_keys` |
| `repr`/`str` del usuario no muestran la contraseña | `apps/accounts/tests/test_password_security.py::test_password_never_appears_in_repr_or_str` |
| Recuperación desde el servidor, solo para ADMIN, sin tocar contadores | `apps/accounts/tests/test_restablecer_admin.py::test_it_only_works_for_administrators`, `apps/accounts/tests/test_restablecer_admin.py::test_it_does_not_touch_the_lock_counters_or_the_lock` |
| El primer administrador: contraseña interactiva; la variable de entorno avisa de borrarla | `apps/accounts/tests/test_crear_admin.py::test_using_the_environment_variable_warns_to_delete_it`, `apps/accounts/tests/test_crear_admin.py::test_the_interactive_path_prints_no_warning_and_no_password` |

## 2. Bloqueo de cuentas y login indistinguible

| Control | Evidencia |
| --- | --- |
| Misma respuesta (código, cuerpo, cabeceras) para correo inexistente, contraseña incorrecta, inactivo, bloqueado y sin contraseña | `apps/accounts/tests/test_login_failures.py::test_every_failure_has_the_same_status_body_and_headers` |
| Siempre exactamente una verificación de hash (hash ficticio precalculado) | `apps/accounts/tests/test_login_failures.py::test_a_hash_is_verified_exactly_once_in_every_case`, `apps/accounts/tests/test_login_failures.py::test_real_argon2_end_to_end_including_the_dummy_hash` |
| Bloqueo en el intento exacto y desbloqueo al vencer (reloj controlado) | `apps/accounts/tests/test_login_failures.py::test_locks_on_the_exact_attempt_and_unlocks_when_it_expires`, `apps/accounts/tests/test_login_failures.py::test_a_failure_after_an_expired_lock_starts_a_new_cycle` |
| Contador atómico (UPDATE único), también entre conexiones | `apps/accounts/tests/test_login_failures.py::test_failure_counter_is_atomic_with_stale_instances`, `apps/accounts/tests/test_login_failures.py::test_failure_counter_is_atomic_across_connections` |
| Ráfagas en paralelo no superan el umbral (FOR NO KEY UPDATE NOWAIT) | `apps/accounts/tests/test_login_failures.py::test_parallel_guesses_cannot_exceed_the_lock_threshold_and_never_skip_the_hash`, `apps/accounts/tests/test_login_lock_errors.py::test_a_real_postgres_row_lock_raises_55P03_and_is_handled_without_waiting` |
| Solo el SQLSTATE 55P03 se trata como contención; cualquier otro error se propaga | `apps/accounts/tests/test_login_lock_errors.py::test_any_other_operational_error_propagates` |
| Sin desbloqueo manual: restablecer, reactivar y desactivar no tocan los contadores | `apps/accounts/tests/test_usuarios_services.py::test_resetting_the_password_does_not_touch_the_lock_counters`, `apps/accounts/tests/test_usuarios_api.py::test_reset_does_not_unlock_a_locked_account`, `apps/accounts/tests/test_usuarios_services.py::test_there_is_no_manual_unlock_in_the_services` |
| El rechazo y el bloqueo quedan en la bitácora; el contador y el evento se confirman aunque la respuesta sea 401 | `apps/accounts/tests/test_auth_events.py::test_counter_and_event_are_committed_even_though_the_response_is_401`, `apps/accounts/tests/test_auth_events.py::test_the_failure_that_locks_the_account_also_records_the_lock` |

## 3. Límite de tasa

| Control | Evidencia |
| --- | --- |
| 5 por minuto por IP+correo y 20 por IP; 429 con `Retry-After`; se decide antes de calcular cualquier hash | `apps/accounts/tests/test_login_failures.py::test_sixth_attempt_in_a_minute_is_429_with_retry_after_and_computes_no_hash`, `apps/accounts/tests/test_login_failures.py::test_ip_only_limit_stops_a_sweep_over_many_emails` |
| Contador compartido entre workers (caché en base de datos, nunca local) | `apps/core/tests/test_ratelimit.py::test_counter_is_shared_and_atomic_across_connections`, `apps/core/tests/test_trusted_proxy.py::test_cache_is_shared_database_cache_not_per_process` |
| El 429 no genera evento (solo una línea de log sin datos sensibles) | `apps/accounts/tests/test_auth_events.py::test_rate_limited_requests_write_no_event_only_a_log_line` |

## 3b. IP real y proxy de confianza

| Control | Evidencia |
| --- | --- |
| `X-Forwarded-*` solo se acepta desde la red de Caddy | `apps/core/tests/test_trusted_proxy.py::test_forwarded_headers_are_dropped_from_untrusted_sources`, `apps/core/tests/test_trusted_proxy.py::test_client_ip_uses_the_address_appended_by_the_proxy` |
| Cabeceras falsificadas no evaden el límite ni engañan la redirección a HTTPS (stack real) | `proxytests/test_proxy_headers.py::test_forged_forwarded_headers_do_not_evade_the_per_ip_and_email_limit`, `proxytests/test_proxy_headers.py::test_forged_x_forwarded_proto_does_not_fool_the_https_redirect`, `proxytests/test_proxy_headers.py::test_control_a_trusted_proxy_can_declare_https` |
| Producción se niega a arrancar con `TRUSTED_PROXY_CIDRS` amplio | `config/tests/test_prod_settings.py::test_validator_rejects_broad_empty_or_invalid_trusted_proxies`, `config/tests/test_prod_settings.py::test_prod_refuses_to_start_when_trusted_proxies_are_left_at_the_default` |

## 4. Sesión y cookies

| Control | Evidencia |
| --- | --- |
| Clave de sesión rotada al iniciar sesión; invalidada en el servidor al cerrar | `apps/accounts/tests/test_session_cookies.py::test_session_key_is_rotated_on_login`, `apps/accounts/tests/test_auth_flow.py::test_logout_returns_204_and_invalidates_the_session_on_the_server`, `apps/accounts/tests/test_auth_flow.py::test_a_stolen_session_cookie_is_useless_after_logout` |
| Expiración por inactividad (30 min, configurable) | `apps/accounts/tests/test_session_cookies.py::test_idle_session_expires_after_30_minutes_and_activity_extends_it`, `apps/accounts/tests/test_session_cookies.py::test_session_idle_time_is_configurable` |
| Desactivar o restablecer cierra las sesiones del afectado | `apps/accounts/tests/test_usuarios_api.py::test_deactivating_closes_the_open_sessions_immediately`, `apps/accounts/tests/test_usuarios_api.py::test_resetting_closes_the_open_sessions_immediately`, `apps/accounts/tests/test_password_change.py::test_changing_the_password_invalidates_the_other_sessions` |
| Cookie `HttpOnly`, `SameSite=Lax`; `Secure` y prefijo `__Host-` en producción; sin cookies en el 401 del login | `apps/accounts/tests/test_session_cookies.py::test_session_cookie_attributes_in_dev`, `apps/accounts/tests/test_session_cookies.py::test_prod_cookies_are_secure_host_prefixed_and_host_compatible`, `config/tests/test_security_headers_prod.py::test_production_cookies_are_secure_httponly_samesite_and_host_prefixed`, `config/tests/test_security_headers_prod.py::test_a_failed_login_and_an_unauthenticated_request_set_no_cookies`, `proxytests/test_cabeceras_cookies.py::test_cookies_through_caddy_in_development_have_the_documented_attributes` (stack real), `proxytests/test_cabeceras_cookies.py::test_production_cookies_are_secure_host_prefixed_and_a_failed_login_sets_none` (stack real) |

## 5. CSRF y CORS

| Control | Evidencia |
| --- | --- |
| CSRF obligatorio en todo POST, también el anónimo (login, logout, cambiar-contrasena) | `apps/accounts/tests/test_session_cookies.py::test_csrf_is_required_for_unsafe_endpoints`, `apps/accounts/tests/test_session_cookies.py::test_a_wrong_csrf_token_is_rejected`, `apps/accounts/tests/test_session_cookies.py::test_csrf_rejection_does_not_touch_the_failure_counter` |
| `CSRF_TRUSTED_ORIGINS` solo desde el entorno | `apps/accounts/tests/test_session_cookies.py::test_csrf_trusted_origins_come_from_the_environment` |
| CORS cerrado | `apps/accounts/tests/test_session_cookies.py::test_cors_is_closed` |

## 6. Cabeceras de seguridad

| Control | Evidencia |
| --- | --- |
| `Cache-Control: no-store` en toda respuesta de `/api/` (también errores) | `apps/core/tests/test_no_store.py::test_every_api_response_is_marked_no_store`, `config/tests/test_security_headers_prod.py::test_every_api_response_is_no_store` |
| `nosniff`, `Referrer-Policy`, `frame-ancestors`, `X-Frame-Options`, COOP | `config/tests/test_security_headers_prod.py::test_https_responses_carry_the_production_security_headers`, `proxytests/test_cabeceras_cookies.py::test_security_headers_on_every_kind_of_response_through_caddy` (stack real), `proxytests/test_cabeceras_cookies.py::test_the_redirect_the_production_api_issues_over_http_also_carries_caddys_headers` (stack real) |
| HSTS solo sobre HTTPS; HTTP se redirige | `config/tests/test_security_headers_prod.py::test_hsts_is_only_sent_over_https_and_http_is_redirected`, `proxytests/test_cabeceras_cookies.py::test_production_security_headers_include_hsts_and_frame_denial` (stack real) |
| Producción: `DEBUG` apagado, hosts y secretos válidos, esquema OpenAPI oculto | `config/tests/test_prod_settings.py::test_prod_refuses_to_start`, `config/tests/test_prod_settings.py::test_every_env_example_secret_is_rejected_by_prod`, `config/tests/test_prod_settings.py::test_prod_hides_schema_and_exempts_health_from_https_redirect`, `proxytests/test_proxy_headers.py::test_the_production_api_hides_the_openapi_schema_through_the_proxy` |
| `/api/v1/health/` solo devuelve el estado | `apps/core/tests/test_health_schema.py::test_health_returns_only_status` |

## 7. Permisos

| Control | Evidencia |
| --- | --- |
| Tabla rol → acciones igual a la sección 3 (escrita aparte en la prueba) | `apps/accounts/tests/test_permission_matrix.py::test_role_action_table_matches_section_3`, `apps/accounts/tests/test_permission_matrix.py::test_every_action_in_the_code_is_in_the_expected_table_and_vice_versa` |
| Matriz rol × acción × método sobre vistas de prueba | `apps/accounts/tests/test_permission_matrix.py::test_endpoint_matrix_role_action_method`, `apps/accounts/tests/test_permission_matrix.py::test_anonymous_gets_401_on_every_action_endpoint` |
| Toda vista declara su permiso; una que no lo hace se deniega; toda ruta está en la matriz | `apps/accounts/tests/test_permission_coverage.py::test_every_real_route_declares_its_permission_and_is_in_the_matrix`, `apps/accounts/tests/test_permission_coverage.py::test_registered_endpoints_follow_their_policy`, `apps/accounts/tests/test_permission_matrix.py::test_a_view_that_declares_no_permissions_is_denied_for_every_role` |
| Los permisos se verifican en el backend (espectador con sesión y CSRF válidos no escribe) | `apps/accounts/tests/test_permission_matrix.py::test_spectator_with_valid_session_and_csrf_cannot_write`, `apps/accounts/tests/test_usuarios_api.py::test_technicians_and_spectators_get_403_on_every_endpoint`, `apps/audit/tests/test_bitacora_api.py::test_technicians_and_spectators_get_403` |
| Inactivos y sesiones de usuarios desactivados no autentican | `apps/accounts/tests/test_permission_matrix.py::test_inactive_users_cannot_authenticate`, `apps/accounts/tests/test_permission_matrix.py::test_sessions_of_deactivated_users_stop_authenticating` |
| Un cambio de rol rige desde la siguiente petición | `apps/accounts/tests/test_permission_matrix.py::test_a_role_downgrade_takes_effect_on_the_next_request`, `apps/accounts/tests/test_usuarios_api.py::test_after_a_role_change_the_open_session_has_the_new_permissions_on_the_next_request` |
| Las denegaciones no generan evento (solo una línea de log) | `apps/accounts/tests/test_permission_matrix.py::test_permission_denials_write_no_event_only_a_log_line` |
| Reglas de administradores: no autodesactivarse, no cambiarse el rol, nunca sin administrador activo (con concurrencia) | `apps/accounts/tests/test_usuarios_services.py::test_an_admin_cannot_deactivate_change_role_or_reset_their_own_password`, `apps/accounts/tests/test_usuarios_services.py::test_an_actor_who_is_no_longer_an_active_admin_is_refused`, `apps/accounts/tests/test_usuarios_concurrencia.py::test_two_admins_cannot_demote_each_other_at_the_same_time`, `apps/accounts/tests/test_usuarios_concurrencia.py::test_two_admins_cannot_deactivate_each_other_at_the_same_time`, `apps/accounts/tests/test_usuarios_concurrencia.py::test_a_cycle_of_three_admins_always_leaves_at_least_one` |

## 8. Bitácora de auditoría

| Control | Evidencia |
| --- | --- |
| Solo inserción en el código | `apps/audit/tests/test_evento_model.py::test_saving_an_existing_event_is_rejected`, `apps/audit/tests/test_evento_model.py::test_deleting_an_event_is_rejected`, `apps/audit/tests/test_evento_model.py::test_queryset_update_delete_and_bulk_update_are_rejected` |
| Solo inserción en la base de datos (el rol `app` no puede UPDATE, DELETE ni TRUNCATE) | `apps/audit/tests/test_real_db_roles.py::test_app_cannot_update_delete_or_truncate_events`, `apps/audit/tests/test_real_db_roles.py::test_privileges_as_reported_by_postgres` |
| El cambio y su evento se confirman o se revierten juntos | `apps/audit/tests/test_registrar_evento.py::test_outside_an_atomic_block_it_raises_and_writes_nothing`, `apps/accounts/tests/test_usuarios_services.py::test_if_the_event_fails_the_change_is_reverted`, `apps/accounts/tests/test_usuarios_services.py::test_if_the_change_fails_after_the_event_no_event_remains` |
| Saneamiento: solo campos de la lista blanca; nunca contraseñas, hashes ni contadores | `apps/audit/tests/test_registrar_evento.py::test_forbidden_fields_are_rejected_and_nothing_is_written`, `apps/audit/tests/test_registrar_evento.py::test_a_real_password_hash_cannot_be_smuggled_in` |
| `SESION_FALLIDA`: una sola inserción por intento y sin guardar el correo escrito | `apps/accounts/tests/test_auth_events.py::test_every_failure_case_does_exactly_one_event_insert`, `apps/accounts/tests/test_auth_events.py::test_unknown_email_writes_one_event_without_user_and_never_stores_the_email` |
| Lectura solo para ADMIN, solo lectura, sin eventos al consultar, sin hashes | `apps/audit/tests/test_bitacora_api.py::test_the_endpoint_is_read_only`, `apps/audit/tests/test_bitacora_api.py::test_reading_the_log_writes_no_event`, `apps/audit/tests/test_bitacora_api.py::test_the_events_query_never_selects_password_hashes`, `apps/audit/tests/test_bitacora_api.py::test_antes_and_despues_never_show_keys_outside_the_whitelist` |
| Paginación por cursor sin duplicados ni omisiones; sin COUNT ni OFFSET | `apps/audit/tests/test_bitacora_api.py::test_walking_every_page_has_no_duplicates_and_no_gaps`, `apps/audit/tests/test_bitacora_api.py::test_new_events_arriving_between_pages_cause_no_duplicates_or_gaps`, `apps/audit/tests/test_bitacora_api.py::test_the_next_page_query_has_no_count_and_no_offset` |
| Rendimiento con 100 000 eventos: índices (sin `Seq Scan`) y tiempos | `apps/audit/tests/test_bitacora_rendimiento.py::test_first_page_uses_the_cursor_index`, `apps/audit/tests/test_bitacora_rendimiento.py::test_a_page_with_date_and_type_filters_does_not_scan_the_table`, `apps/audit/tests/test_bitacora_rendimiento.py::test_response_times_stay_within_the_budget` |

## 9. Base de datos y contenedores

| Control | Evidencia |
| --- | --- |
| Dos roles: `migrator` (dueño) y `app` (sin DDL, sin superusuario); privilegios por defecto sobre tablas nuevas | `apps/core/tests/test_db_roles.py::test_app_gets_dml_on_tables_created_by_migrator`, `apps/core/tests/test_db_roles.py::test_app_cannot_change_the_schema`, `apps/core/tests/test_db_roles.py::test_app_is_not_superuser_nor_owner` |
| Contenedores sin root, `cap_drop ALL`, `no-new-privileges`, sistema de archivos de solo lectura, BD sin puerto publicado, único puerto en `127.0.0.1`, volumen persistente, límite de memoria | `sh deploy/verificar_contenedores.sh <proyecto>` (corre dentro de `make test-proxy`; `make check-containers` sobre el entorno de desarrollo) |
| El compose y el Caddyfile son válidos | `make lint` |

## 10. Secretos y dependencias

| Control | Evidencia |
| --- | --- |
| Sin secretos en el repositorio ni en el historial (gitleaks, historial y archivos) | `make secrets` |
| `.env` fuera de git y todos los secretos de `.env.example` marcados como de ejemplo y rechazados por producción (ver también la sección 6) | `config/tests/test_prod_settings.py::test_the_env_example_value_is_rejected_by_prod`, `git check-ignore .env` |
| Dependencias bloqueadas con hashes, instaladas con `--require-hashes` y auditadas con pip-audit | `make audit`, `make lock` |
| Cobertura de código ≥ 85 % (incluye `settings/prod.py`, medido también en subprocesos) | `make test` |
