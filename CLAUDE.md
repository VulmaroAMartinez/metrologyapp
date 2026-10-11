# Sistema de control de instrumentos de metrología
Fuente de verdad: docs/DEFINICION.md (modelo en sección 5, reglas en sección 6, permisos en sección 3).
Si algo no está definido, revisa la sección 13 y pregunta; no inventes reglas de negocio.

## Comandos (se actualizan al terminar el sprint 1)
- make up | make test | make lint | make ci

## Estructura (backend/apps)
- `accounts/services/{autenticacion,usuarios}.py`: reglas de negocio de cuentas; `services/__init__.py` reexporta la API.
- `accounts/locks.py`: candados (L0 administradores, L1 fila); `accounts/permissions.py`: matriz rol -> acciones.
- `audit/`: bitácora (`Evento`, solo inserción). `core/`: red, límite de tasa, errores, `constantes.py`, `testing.py` (ayudantes de prueba, no es código de producción).
- Pruebas junto a cada app (`tests/`); fixtures comunes en `backend/conftest.py`; sin `mock.patch` sobre internos de `services` (usa `sustituir(...)`).

## Nombres
Dominio en español (usuario, correo, bitácora, candado, rol); infraestructura y utilidades genéricas en inglés (`client_ip`, `check_rate`, `ratelimit`). No renombrar lo existente por esta regla.

## Reglas
- Reglas de negocio en la capa de servicios, no en vistas ni serializadores.
- Toda operación que cambia datos de negocio escribe su Evento en la misma transacción.
- Sin borrados físicos de datos de negocio.
- Fechas de calendario como DATE; momentos en UTC-6 (Mexico City).
- Interfaz en español. Secretos solo en variables de entorno.
- No des una tarea por terminada sin haber corrido pruebas y linter.

## Criterio de eventos de la bitácora
Solo se registra un evento cuando cambian datos de negocio o hay una acción relevante para la seguridad. No se registran: consultas ni descargas, cierres ni expiraciones de sesión, rechazos por límite de tasa, denegaciones de permiso, ni acciones repetitivas del sistema sin cambio de estado. Antes de agregar un tipo de evento nuevo, pregunta a Vul.