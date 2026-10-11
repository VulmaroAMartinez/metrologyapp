# Pendientes y trabajo diferido

Lo que se decidió **no** hacer en el sprint 1, con el sprint donde corresponde. Las decisiones que tocan
a Vul están en `docs/decisiones.md` («Pendiente para la v1.2» y «Decisiones tomadas por Claude Code»).

## Sprint 2 (catálogos e instrumentos)
- Lista blanca de `apps/audit/sanitize.py` **por tipo de evento**, con soporte de `Decimal` y `UUID` (hoy
  solo valores escalares simples y una lista única).
- Índices de `Evento`: uno por `tipo` y uno **parcial** para `instrumento_id` (no nulo) cuando exista la FK.
- Consultas con `Lower(...)` (búsqueda sin distinguir mayúsculas) con índices funcionales, al llegar a los
  catálogos y al listado de instrumentos.
- Parser `multipart` **solo** en la vista de subida de PDF (hoy la API solo acepta JSON).

## Sprint 4 (avisos y proceso diario)
- Purga de `django_cache` (la tabla de la caché en base de datos no se limpia sola) y de `django_session`
  (`clearsessions`, ya previsto en el proceso diario).

## Sprint 6 (cierre)
- Configuración de producción con TLS (certificado automático o interno) y HSTS escalonado.
- Gancho de pruebas del `Caddyfile` (hoy solo `caddy validate`).
- Imagen de producción sin herramientas de prueba (hoy `jq` y las pruebas solo están en la etapa `test`).
- Costo de `check_rate` (una escritura en base de datos por intento): medir y decidir si cambia de estrategia.
- Actualización de hashes Argon2 si cambian los parámetros (hoy el login no re-cifra hashes antiguos): política
  y prueba.
- Pruebas que usan `sleep` (las de proxy esperan a la ventana siguiente): reemplazar por reloj controlado donde
  sea posible.
- Firewall del host (`DOCKER-USER`) para cerrar la salida a internet del proxy; en Docker Desktop se midió que
  alcanza internet aun con el NAT desactivado.
- Logs con identificador de petición (correlación sin datos sensibles).
- Escalado de HSTS (1 h → 1 día → 1 semana → 1 año) y decisión sobre `includeSubDomains`/preload.
- Capas de protección del hook `proteger-archivos.sh` (hoy solo `Edit|Write`; un `Bash` con `sed -i` no pasa por
  él; hipótesis de evasión por alias de ruta sin probar).
- Reservar el intento en el login si el motivo `intento_simultaneo` muestra colisiones reales.

## Integración continua (de la modo autónomo)
- El flujo `.github/workflows/ci.yml` **no se ha ejecutado en GitHub** (no hay push ni token en esta sesión):
  la primera ejecución real puede mostrar ajustes (p. ej. el tiempo de las pruebas de proxy en el ejecutor).
- La caché del flujo guarda solo las imágenes base; la capa de `pip install` de la imagen de pruebas no se
  cachea (requeriría `buildx bake` con caché `gha`).
- Dependabot vigila `/frontend` (npm y Docker) pero aún no hay `package.json`: hasta el sprint del frontend
  mostrará un aviso en esa entrada.
- Los SHA de las acciones se resolvieron el 10-oct-2026 (`actions/checkout` v7.0.1, `actions/cache` v6.1.0);
  Dependabot los actualizará.

## Parte 2 del modo autónomo
No iniciada (fuera del alcance de esta ronda).
