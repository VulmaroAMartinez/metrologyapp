## Qué cambia y por qué

<!-- Una o dos frases. Enlaza la sección de docs/DEFINICION.md o la decisión de docs/decisiones.md. -->

## Pruebas
- [ ] `make ci` en verde (lint, schema, audit, test, test-proxy, secrets)
- [ ] Pruebas nuevas o ajustadas para el cambio (primero la prueba que falla)
- [ ] Cobertura sin bajar del 95 % (ramas)

## Seguridad y reglas del proyecto
- [ ] Sin secretos en el código ni en los archivos de ejemplo
- [ ] Reglas de negocio en la capa de servicios; el evento se escribe en la misma transacción
- [ ] Sin borrados físicos de datos de negocio; entradas validadas en la frontera
- [ ] Permisos: la vista declara su acción y está en la matriz (DEFINICION §3)
- [ ] Ningún tipo de evento nuevo sin aprobación de Vul
- [ ] Cambios en `docs/seguridad-checklist.md` si cambia un control
- [ ] Interfaz y mensajes en español
