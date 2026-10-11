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
| 2 – Refactor sin cambio de comportamiento | `59f51e0` (2A pruebas), `8026124` (2B servicios), `32457a6` (2C limpieza) | verde, 185 s en 2B y 2C (lint 4, schema 3, audit 11, test 95, test-proxy 62, secrets 10) | hecha: 1199 pruebas antes y después del refactor, 1200 con el ancla de `query_params`; esquema OpenAPI idéntico; 3 violaciones deliberadas de las pruebas `ast` detectadas |
| 3 – CI y documentación | `7c224ff` (ci), `docs:` Fase 3 (ver `git log`) | verde, 186 s (lint 4, schema 3, audit 11, test 97, test-proxy 61, secrets 10) | hecha: el flujo de GitHub no se ha ejecutado (sin push) |
| 4 – Modelo de amenazas | `docs:` Fase 4 (ver `git log`) | ver abajo | hecha |

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

Un comando de prueba del hook de archivos protegidos fue bloqueado por una comprobación de seguridad del sistema; no se rodeó (ver «Pendientes»). Ningún otro.

## Pendientes

Los diferidos están en `docs/pendientes.md`. Abiertos de esta ejecución:

- La parte 2 del modo autónomo **no está empezada**.
- `.github/workflows/ci.yml` nunca corrió en GitHub (sin push): la primera ejecución real puede pedir ajustes.
- Hipótesis de evasión del hook `proteger-archivos.sh` por alias de ruta: el comando de prueba lo bloqueó una
  comprobación de seguridad y no se rodeó; sigue sin probar.
- Proxy con salida a internet en Docker Desktop (medido); regla `DOCKER-USER` sin verificar en Linux.
- Decisiones de Vul pendientes: `docs/decisiones.md` («Decisiones tomadas por Claude Code (revisar)» y
  «Pendiente para la v1.2»).
