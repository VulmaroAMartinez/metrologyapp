# Sistema de control de instrumentos de metrología
Fuente de verdad: docs/DEFINICION.md (modelo en sección 5, reglas en sección 6, permisos en sección 3).
Si algo no está definido, revisa la sección 13 y pregunta; no inventes reglas de negocio.

## Comandos (se actualizan al terminar el sprint 1)
- make up | make test | make lint | make ci

## Reglas
- Reglas de negocio en la capa de servicios, no en vistas ni serializadores.
- Toda operación que cambia datos de negocio escribe su Evento en la misma transacción.
- Sin borrados físicos de datos de negocio.
- Fechas de calendario como DATE; momentos en UTC-6 (Mexico City).
- Interfaz en español. Secretos solo en variables de entorno.
- No des una tarea por terminada sin haber corrido pruebas y linter.