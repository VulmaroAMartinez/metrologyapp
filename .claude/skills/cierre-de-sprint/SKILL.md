---
name: cierre-de-sprint
description: Cierra un sprint: corre verificaciones, revisa la definición de terminado y actualiza la documentación.
disable-model-invocation: true
allowed-tools: Bash(make *) Bash(git status *) Bash(git log *)
---
## Estado actual
!`git status --short`
!`git log --oneline -15`

## Pasos
1. Ejecuta `make ci`. Si hay fallas, repórtalas y detente.
2. Verifica la definición de terminado y la checklist de seguridad con evidencia.
3. Actualiza CLAUDE.md, README y docs/decisiones.md.
4. Entrega: hecho, pendiente, supuestos, riesgos y decisiones abiertas nuevas.