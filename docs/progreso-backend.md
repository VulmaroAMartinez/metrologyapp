# Progreso del backend del sprint 1 (modo autónomo, parte 1 de 2)

Documento para **reanudar**: dice en qué fase se quedó el trabajo, con qué commit y qué falta.
Se actualiza al cerrar cada fase. La parte 2 NO está empezada.

## Reglas de esta ejecución

- Sin `git push`, sin etiquetas, sin borrar nada fuera del repositorio.
- Cada fase termina con `make ci` en verde, `git status` limpio y un commit.
- Si el sistema bloquea un comando, no se rodea: se anota abajo («Bloqueos») y se continúa.
- Las decisiones que tomó Claude Code están en `docs/decisiones.md`, sección «Decisiones tomadas
  por Claude Code (revisar)».

## Fases

| Fase | Commit | Resultado de `make ci` | Estado |
| --- | --- | --- | --- |
| 0 – Línea base | (sin commit) | verde, 200 s (lint 8, schema 4, audit 14, test 102, test-proxy 61, secrets 11) | hecha |
| 1 – Pruebas deterministas | `test: pruebas deterministas` | verde, 181 s (lint 4, schema 3, audit 11, test 94, test-proxy 59, secrets 10) | hecha: 10/10 corridas de proxy y 10/10 de límite de tasa; `jq` ausente de la imagen de producción |
| 2 – Refactor sin cambio de comportamiento | pendiente | pendiente | en curso |
| 3 – CI y documentación | pendiente | pendiente | sin empezar |
| 4 – Modelo de amenazas | pendiente | pendiente | sin empezar |

## Línea base (fase 0)

```
$ git status --short            # vacío
$ git status -sb | head -1      ## sprint-1...origin/sprint-1
$ git log --oneline -3
51480a9 fix: las sesiones antiguas no reviven al reactivar un usuario
2dc24e1 fix: errores y validaciones unificados
5dbf27f fix: el login ya no choca con la administración
```

## Bloqueos del sistema (comandos que no se rodearon)

Ninguno hasta ahora.

## Pendientes

Los diferidos están en `docs/pendientes.md` (se crea en la fase 3).
