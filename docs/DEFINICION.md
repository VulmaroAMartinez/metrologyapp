# Sistema de control de instrumentos de metrología

**Definición del proyecto · v1.1 · 9 de octubre de 2026** Equipo: Vul + Claude Code · Plazo de la primera versión: 3 meses

## 0. Cómo leer este documento

- **\[DECIDIDO\]**: lo confirmó Vul. **\[PROPUESTA\]**: recomendación técnica todavía sin confirmar; puede cambiar.
- La sección 13 reúne las decisiones abiertas y la sección 14 lista los cambios respecto a la v1.0. Claude Code no debe inventar reglas de negocio: si algo no está definido, consulta la sección 13 y pregunta a Vul.
- Este archivo está protegido: Claude Code no lo edita. Los cambios los hace Vul, a partir de docs/decisiones.md.
- Lo que no esté en el alcance de la sección 2 queda fuera de la v1.

## 1. Resumen

Sistema web para el área de metrología que **reemplaza el Excel** con el que hoy se controlan los instrumentos. Registra instrumentos y su historial de calibraciones, guarda el certificado PDF de cada calibración, calcula fechas de caducidad, envía avisos por correo y mantiene una bitácora de auditoría. \[DECIDIDO\]

Se **vende a empresas** con área de metrología y se **instala una vez por cliente**, en el servidor Linux de la propia empresa. Una instalación equivale a una empresa: no hay multiempresa ni SaaS. Cada empresa tendrá **dos administradores**, y el sistema se instala **solo en red interna o VPN**, nunca expuesto directo a internet. \[DECIDIDO\]

## 2. Alcance

**Dentro de la v1** \[DECIDIDO\]:

- Usuarios con 3 roles, gestionados por el administrador, que también asigna y restablece las contraseñas.
- Catálogos: áreas, tipos de instrumento, magnitudes y unidades.
- Instrumentos: alta (con calibración inicial y certificado), edición, cambio de estado y de área, baja.
- Calibraciones y certificados PDF, con eliminación lógica de certificados.
- Reemplazos (instrumento de respaldo).
- Avisos por correo con fechas manuales por instrumento y paso automático a Caducado.
- Timeline por instrumento y bitácora de auditoría.
- Dashboard, calendario y listado con filtros.
- Reportes PDF: timeline del instrumento y certificados del instrumento.
- Instalación con Docker Compose, respaldos y guía de instalación \[PROPUESTA\].

**Fuera de la v1:** multiempresa o SaaS; carga inicial desde Excel (todo se captura a mano); el tercer reporte (se definirá más adelante); automatización de reglas de avisos (por ejemplo 30, 15 y 7 días antes); registro de consultas y descargas; exportación a Excel; desbloqueo manual de cuentas; recuperación de contraseña por correo; cambio de contraseña por técnicos y espectadores; idiomas distintos del español.

## 3. Usuarios y permisos \[DECIDIDO salvo las filas marcadas\]

| Acción | Administrador | Técnico | Espectador |
| --- | --- | --- | --- |
| Consultar instrumentos, calibraciones, certificados, timeline, dashboard, calendario y reportes | Sí | Sí | Sí |
| Crear y editar instrumentos, cambiar estado o área, dar de baja, registrar calibraciones, subir certificados, gestionar reemplazos | Sí | Sí | No |
| Crear, editar y cancelar avisos | Sí | Sí | No |
| Eliminar certificados (con motivo) | Sí | No | No |
| Ver la bitácora de auditoría completa | Sí | No | No |
| Gestionar usuarios: crear, editar, cambiar rol, desactivar, reactivar y asignar o restablecer la contraseña de otro usuario | Sí | No | No |
| Cambiar la propia contraseña | Sí | No | No |
| Gestionar catálogos y configuración \[PROPUESTA\] | Sí | No | No |
| Recibir correos de caducidad | Sí | Sí | No |

- Los permisos se verifican siempre en el backend; ocultar botones en el frontend no basta. El rol se lee de la base de datos en cada petición, así que un cambio de rol aplica de inmediato sin cerrar la sesión.
- Toda vista declara su permiso; una vista sin permiso declarado se deniega. Errores con forma {codigo, detalle}: no\_autenticado (401), permiso\_denegado (403), csrf\_invalido, datos\_invalidos, metodo\_no\_permitido. Sin permiso es siempre 403, nunca 404.

## 4. Stack y arquitectura

| Capa | Tecnología |
| --- | --- |
| Frontend | Next.js (App Router, TypeScript estricto) \[DECIDIDO\] |
| Backend | Django + Django REST Framework \[DECIDIDO\] |
| Base de datos | PostgreSQL \[DECIDIDO\] |
| API | REST bajo /api/v1, esquema OpenAPI con drf-spectacular y cliente TypeScript generado con openapi-typescript \[DECIDIDO\] |
| Interfaz | Tailwind \[DECIDIDO\]; shadcn/ui, TanStack Table y FullCalendar \[PROPUESTA: Claude Code pregunta antes de añadir librerías\] |
| Autenticación | Sesión en base de datos con cookie HttpOnly y modelo de usuario propio con rol; hash Argon2 \[DECIDIDO\] |
| Caché | Caché en base de datos, compartida entre los workers \[DECIDIDO\] |
| Correo | send\_mail de Django con SMTP configurado por variables de entorno \[PROPUESTA\] |
| Proceso diario | Comando manage.py proceso\_diario ejecutado por cron en un contenedor aparte \[PROPUESTA\] |
| Reportes PDF | WeasyPrint para el timeline y pypdf para unir certificados \[PROPUESTA\] |
| Despliegue | Docker Compose con web (Next.js), api (Django + gunicorn), db, cron y proxy (Caddy) \[DECIDIDO\] |

Versiones fijadas en el repositorio con tag exacto y digest (Python 3.14, Django 5.2 LTS, PostgreSQL 18, Node 24 LTS, Caddy 2); el detalle vive en docs/decisiones.md. Next.js y Django van detrás del mismo dominio: /api va a Django y el resto a Next.js, de modo que las cookies son del mismo origen y el CORS permanece cerrado. \[DECIDIDO\]

Identificadores: el dominio en español (modelos, campos, roles y tipos de evento, tal como en este documento) y lo técnico y genérico en inglés; interfaz en español. \[DECIDIDO\]

Estructura del repositorio:

```
backend/   apps Django: accounts, audit, core (más catalogs, instruments, calibrations, notifications, reports en sprints siguientes)
frontend/  Next.js
deploy/    docker-compose.yml, Caddyfile, scripts de instalación, respaldo y verificación
docs/      este documento, decisiones.md, guia-instalacion.md, seguridad-checklist.md
```

## 5. Modelo de datos

Reglas generales \[PROPUESTA\]: nunca se borra físicamente un registro de negocio; las fechas de calendario son DATE y los momentos son datetime en UTC; la zona horaria local se configura por instalación (America/Mexico\_City por defecto).

| Tabla | Campos | Restricciones |
| --- | --- | --- |
| Usuario | nombre, correo, numero\_nomina, rol (ADMIN, TECNICO, ESPECTADOR), activo, contraseña (hash), intentos\_fallidos, bloqueado\_hasta, creado\_en | correo único sin distinguir mayúsculas y guardado en minúsculas; numero\_nomina opcional: NULL si no existe (nunca vacío) y único cuando tiene valor, sin distinguir mayúsculas ni espacios; rol con restricción; intentos\_fallidos no negativo; sin is\_staff ni is\_superuser |
| Area | codigo (3 dígitos), nombre, activa | codigo único |
| TipoInstrumento | codigo (2 letras), nombre, activo | codigo único |
| Magnitud | nombre, activa | nombre único |
| Unidad | magnitud, simbolo, nombre, activa | único (magnitud, simbolo); si está en uso solo se desactiva |
| Instrumento | id interno inmutable; codigo (ID de negocio); area\_origen, linea (A a L o Z), numero, tipo; area\_actual; asignado (texto: máquina, persona, etc.); marca; modelo; numero\_serie; connection\_pipe (texto: código de la tubería a la que está conectado); magnitud; unidad; alcance\_min; alcance\_max; resolucion; error\_maximo\_permitido (en la unidad del instrumento); frecuencia\_calibracion\_meses (entero mayor que 0); frecuencia\_verificacion (texto); responsable\_verificacion (texto); estado; creado\_por; creado\_en | codigo único; (area\_origen, linea, numero, tipo) único; alcance\_min no mayor que alcance\_max; la unidad debe pertenecer a la magnitud |
| Calibracion | instrumento, numero\_certificado, fecha\_calibracion (DATE), proxima\_fecha (DATE, calculada), registrada\_por, registrada\_en | proxima\_fecha no se acepta por la API; la vigente es la de mayor fecha\_calibracion |
| Certificado | calibracion, archivo, nombre\_original, tamano\_bytes, sha256, subido\_por, subido\_en, eliminado\_en, eliminado\_por, motivo\_eliminacion | un solo certificado activo (eliminado\_en nulo) por calibración, con índice único parcial |
| Reemplazo | reemplazante, reemplazado, desde, hasta, creado\_por, cerrado\_por | a lo sumo un reemplazo abierto por reemplazado y por reemplazante |
| Aviso | instrumento, fecha\_envio (DATE), creado\_por, creado\_en, enviado\_en, cancelado\_en, cancelado\_por, error | editable mientras enviado\_en y cancelado\_en sean nulos |
| Evento | ocurrido\_en (UTC), usuario (nulo = Sistema), instrumento\_id (entero sin llave foránea hasta el sprint 2), tipo, descripcion, antes (JSON), despues (JSON), ip | solo inserción en el código y en la base de datos; tipo con lista cerrada (agregar uno exige una migración); antes y despues aceptan solo una lista blanca de campos, nunca contraseñas ni contadores; índices por (ocurrido\_en, id), usuario, tipo e instrumento\_id |
| Configuracion | clave, valor | ventana de próximos a vencer (30 días), tamaño máximo de PDF (10 MB) |

Django agrega django\_session y django\_cache; las tablas auth\_group y auth\_permission existen pero no se usan.

### 5.1 Datos semilla \[DECIDIDO para áreas y tipos; PROPUESTA para magnitudes y unidades\]

**Áreas:** Frick 000, Descongelado 050, Eviscerado 100, Cocimiento 150, Spray Room 200, Chill Room 250, Limpieza de atún 300, Calidad 350, Enlatado 400, I + D 450, CMA 500, Tacore 550, Esterilizado 600, Etiquetado 650, Almacén de químicos 700, Calderas 750, Mantenimiento 800, Cuarto de máquinas 850, PETAR 900, Harinera 950.

**Tipos de instrumento:** Indicador de temperatura TI, Transmisor de temperatura TT, Indicador de presión PI, Transmisor de presión PT, Indicador de flujo FI, Transmisor de flujo FT, Indicador de peso WI, Transmisor de peso WT, Indicador de dimensión DI, Transmisor de dimensión DT, Indicador de volumen VI, Transmisor de volumen VT.

**Magnitudes y unidades iniciales:** longitud (mm, m, in), masa (g, kg, lb), tiempo (s, min, h), temperatura (°C, °F, K), presión (bar, psi, kPa, MPa), flujo (L/min, m³/h), volumen (L, m³). El administrador puede agregar más. Las áreas y los tipos corresponden a la primera empresa; cada instalación puede tener los suyos, por eso son catálogos.

## 6. Reglas de negocio

### 6.1 ID del instrumento \[DECIDIDO\]

Formato: **Área (3 dígitos) + Línea (A a L o Z) + Número (manual) + Tipo (2 letras)**. Ejemplo: 650C01WI = Etiquetado, línea C, número 01, indicador de peso.

- El ID completo es único en toda la base de datos y se valida contra el formato y los catálogos.
- El ID **nunca cambia**, ni al mover el instrumento de área. El área actual es un campo aparte, y los filtros por área usan area\_actual, nunca el prefijo del ID.
- Se guarda desglosado (area\_origen, linea, numero, tipo) y armado (codigo). Al capturar, el sistema puede sugerir el siguiente número libre. \[PROPUESTA\]

### 6.2 Estados \[DECIDIDO\]

En operación, Fuera de servicio, Baja y Caducado.

| De | A | Cómo ocurre |
| --- | --- | --- |
| En operación | Fuera de servicio | Manual (avería o envío a calibrar) |
| Fuera de servicio | En operación | Manual (reparado, calibrado o asignado como reemplazo) |
| En operación o Fuera de servicio | Caducado | Automático, por el proceso diario (6.5) |
| Caducado | En operación | Al registrar una nueva calibración (ver decisión abierta 4) |
| Cualquiera | Baja | Manual |

**Baja es final**: no se puede revertir, pide confirmación y conserva todo el historial y los certificados.

### 6.3 Alta de instrumento \[DECIDIDO\]

El alta es **atómica**: en una sola transacción se crean el instrumento, su calibración inicial y su certificado PDF. No se puede registrar un instrumento sin calibración, así que todo instrumento tiene siempre al menos una calibración y una próxima fecha. Si algo falla, no se guarda nada.

### 6.4 Calibraciones y fechas \[DECIDIDO\]

- La próxima fecha se calcula sola: **fecha de calibración + frecuencia en meses**, con aritmética de meses que respete fin de mes (31 de enero + 1 mes = 28 de febrero).
- Se guarda en la calibración como foto de ese momento y no se edita a mano.
- La calibración vigente es la de mayor fecha\_calibracion, no la última subida.
- Cada nueva calibración exige su certificado PDF.
- Si cambia la frecuencia, aplica desde la siguiente calibración. Un instrumento sigue vigente durante el día de su próxima fecha y caduca al día siguiente. \[PROPUESTA\]

### 6.5 Caducidad automática \[DECIDIDO\]

El proceso diario pasa a **Caducado** todo instrumento En operación **o Fuera de servicio** (los de respaldo también requieren calibración) cuya próxima fecha vigente sea anterior a la fecha local de hoy. Ignora Baja y los ya Caducados. Cada cambio genera un evento con usuario Sistema. El mismo proceso limpia las sesiones vencidas (clearsessions).

### 6.6 Reemplazos (instrumento de respaldo) \[DECIDIDO\]

A es el instrumento en uso y B su respaldo, dado de alta como Fuera de servicio.

1. A pasa a Fuera de servicio (avería o envío a calibrar).
2. El técnico asigna a B como reemplazo de A: B toma el área actual y el asignado de A, pasa a En operación y se crea un Reemplazo abierto. Si B tiene la calibración vencida, el sistema advierte antes de permitirlo. \[PROPUESTA\]
3. Cuando A regresa (reparado o recién calibrado; si fue calibración se registra con su certificado), A pasa a En operación y B regresa a Fuera de servicio con su área y asignado previos \[PROPUESTA en los datos previos\]. Se cierra el Reemplazo.

El evento de salida de servicio del paso 3 debe decir **Instrumento B Fuera de servicio**. En general, cada evento nombra al instrumento al que le ocurre.

### 6.7 Certificados \[DECIDIDO\]

- Un solo certificado, en PDF, por calibración.
- El sistema valida el contenido del archivo y no solo la extensión; tamaño máximo configurable (10 MB por defecto); se guarda su huella SHA-256. \[PROPUESTA\]
- Archivos en una carpeta persistente del servidor (volumen de Docker) con nombres aleatorios. La descarga pasa siempre por un endpoint autenticado, nunca por una ruta pública. \[PROPUESTA\]
- Solo el administrador puede eliminar un certificado, siempre con motivo. La eliminación es **lógica**: el archivo se conserva, la calibración queda sin certificado y se puede subir el correcto. El evento guarda quién, cuándo y por qué.

### 6.8 Avisos por correo \[DECIDIDO salvo indicación\]

- Las fechas de aviso se **capturan a mano por instrumento**; cada una es un registro en Aviso. La automatización queda para después y debe poder funcionar solo generando registros de Aviso.
- Los crean, editan y cancelan el administrador y los técnicos mientras no se hayan enviado.
- Los reciben **todos los técnicos y administradores activos**.
- El proceso diario envía los avisos con fecha de envío menor o igual a hoy que no estén enviados ni cancelados, y guarda el resultado en el propio Aviso (enviado\_en o error).
- Al registrar una nueva calibración se **cancelan los avisos pendientes** del ciclo anterior.
- Los avisos **no generan eventos** de bitácora. Si un envío falla, queda en Aviso.error, se reintenta en la siguiente corrida y se cuenta en el resumen del proceso diario.
- Al pasar un instrumento a Caducado se envía un correo automático. \[PROPUESTA\]

### 6.9 Eventos: timeline y bitácora \[DECIDIDO\]

Cada evento guarda fecha y hora, usuario (o Sistema), instrumento si aplica, tipo, descripción legible, datos antes y después e IP. La fecha va en la columna; el formato `dd-mm-aaaa hh:mm · ID · texto · usuario` se arma al mostrar.

**Criterio:** solo se registra un evento cuando cambian datos de negocio o hay una acción relevante para la seguridad. No se registran consultas ni descargas, cierres ni expiraciones de sesión, eventos de avisos, rechazos por límite de tasa ni denegaciones de permiso (estos dos últimos dejan solo una línea de log sin datos sensibles).

Tipos vigentes: INSTRUMENTO\_ALTA, INSTRUMENTO\_EDITADO, INSTRUMENTO\_CAMBIO\_AREA, INSTRUMENTO\_CAMBIO\_ESTADO (de → a, con motivo), CALIBRACION\_REGISTRADA, CERTIFICADO\_SUBIDO, CERTIFICADO\_ELIMINADO, REEMPLAZO\_INICIADO, REEMPLAZO\_TERMINADO, USUARIO\_CREADO, USUARIO\_EDITADO, USUARIO\_ROL\_CAMBIADO, USUARIO\_DESACTIVADO, USUARIO\_REACTIVADO, USUARIO\_CONTRASENA\_RESTABLECIDA, USUARIO\_CONTRASENA\_CAMBIADA, SESION\_INICIADA, SESION\_FALLIDA, CUENTA\_BLOQUEADA, CATALOGO\_MODIFICADO, CONFIGURACION\_CAMBIADA y PROCESO\_DIARIO\_EJECUTADO (un resumen por corrida: avisos enviados y fallidos, instrumentos pasados a Caducado). Los tipos de instrumentos, calibraciones, certificados, reemplazos, catálogos y configuración se activan en los sprints que los construyen.

- SESION\_FALLIDA se escribe una vez por intento fallido, exista o no el usuario; para un correo inexistente no se guarda el correo. El intento que bloquea la cuenta escribe además CUENTA\_BLOQUEADA.
- Los cambios automáticos generan eventos con usuario Sistema.
- **Timeline del instrumento** (los 3 roles): eventos INSTRUMENTO\_*, CALIBRACION\_*, CERTIFICADO\_\* y REEMPLAZO\_\* de ese instrumento. **Bitácora** (solo administrador): todos los eventos, en GET /api/v1/bitacora/eventos/ (solo lectura, paginación por cursor de 25 y máximo 100, sin COUNT ni OFFSET), con filtros por fecha local, usuario (id o sistema), tipo e instrumento\_id.
- El evento se escribe en la misma transacción que el cambio: si falla uno, falla el otro. \[DECIDIDO\]
- La tabla es solo de inserción: el código lo impide y el rol de base de datos de la aplicación no tiene UPDATE, DELETE ni TRUNCATE sobre ella. El rol dueño del esquema (migrator) solo se usa en migraciones y pruebas.

### 6.10 Cuentas, contraseñas y sesiones \[DECIDIDO salvo los valores provisionales\]

- **Solo el administrador asigna y restablece contraseñas.** Técnicos y espectadores no pueden cambiar la suya. No hay cambio forzado al entrar ni contraseña temporal generada: el administrador escribe la contraseña (campo contrasena), que pasa por los validadores y nunca se devuelve ni se registra en eventos, logs o errores. Se acepta que el administrador conozca las contraseñas de los usuarios; se les advierte en la guía.
- El administrador cambia la propia con su contraseña actual (nueva distinta, límite de 5 por minuto, invalida las otras sesiones).
- Un administrador no puede desactivarse, cambiarse el rol ni restablecerse a sí mismo por la ruta de administración, y el sistema nunca queda sin un administrador activo (candado dentro de la transacción). Restablecer la contraseña de otro, incluido el otro administrador, invalida sus sesiones y no toca sus contadores de bloqueo.
- **Sin desbloqueo manual:** una cuenta bloqueada solo se libera al vencer bloqueado\_hasta. Consecuencia aceptada: una cuenta atacada de forma repetida puede quedar bloqueada de forma intermitente. Mitigaciones: evento CUENTA\_BLOQUEADA, límite por IP y red interna o VPN.
- Recuperación de emergencia: manage.py restablecer\_admin \<correo> (solo ADMIN, contraseña interactiva, evento con usuario Sistema, no toca el bloqueo). Primer administrador: manage.py crear\_admin, interactivo; la variable de entorno solo para instalaciones automatizadas, que el script de instalación borra del .env.
- **Login:** correo inexistente, contraseña incorrecta, usuario inactivo y cuenta bloqueada dan el mismo 401 (mismo cuerpo y cabeceras) y siempre ejecutan una verificación de hash. El contador de fallos se actualiza con un UPDATE atómico y bloquea al llegar al límite. Los intentos simultáneos contra una misma cuenta usan FOR NO KEY UPDATE NOWAIT: quien no obtiene el candado recibe el mismo 401 tras un hash ficticio (costo aceptado: un intento legítimo que coincida puede tener que reintentar).
- **Límite de tasa** por IP más correo y por IP, evaluado antes de calcular cualquier hash, con 429 y Retry-After y contador compartido entre workers. La IP real viene de Caddy; Django descarta X-Forwarded-\* de cualquier otro origen y producción exige acotar los proxies de confianza.
- **Sesión:** cookie HttpOnly, SameSite=Lax (Secure y prefijo \_\_Host- en producción), guardada en base de datos, rotada al iniciar sesión, con expiración deslizante por inactividad. CSRF obligatorio en todo POST, también el login; el token se pide a /api/v1/auth/csrf/ y rota al iniciar sesión.

| Valor provisional | Valor | Variable |
| --- | --- | --- |
| Longitud mínima de contraseña | 10 | PASSWORD\_MIN\_LENGTH |
| Intentos fallidos antes de bloquear | 5 | LOGIN\_MAX\_FAILED\_ATTEMPTS |
| Duración del bloqueo | 15 min | LOGIN\_LOCKOUT\_MINUTES |
| Login por IP + correo | 5 por minuto | LOGIN\_RATE\_PER\_IP\_EMAIL |
| Login por IP | 20 por minuto | LOGIN\_RATE\_PER\_IP |
| Inactividad | 30 min | SESSION\_IDLE\_MINUTES |

## 7. Pantallas y reportes

- **Dashboard de inicio** \[DECIDIDO\]: instrumentos por estado, cuántos caducan en los próximos 30 días (configurable), lista de Caducados y gráfica por área \[PROPUESTA en el detalle\].
- **Calendario** \[DECIDIDO\]: vista mensual, semanal y de lista; cada instrumento aparece una vez, en el día de su próxima fecha vigente, con colores para futuros, próximos a vencer y Caducados; clic abre la ficha; filtros por área, tipo y estado; sin instrumentos en Baja.
- **Listado de instrumentos** \[PROPUESTA\]: búsqueda, filtros por área actual, tipo, magnitud, estado y rango de próxima fecha, y orden por columna.
- **Ficha del instrumento** \[PROPUESTA\]: datos, historial de calibraciones con descarga del PDF, avisos y timeline. **Alta y edición:** formulario con la calibración inicial en la misma pantalla.
- **Administración** (solo administrador): usuarios (listar, crear, editar, cambiar rol, desactivar, reactivar y restablecer contraseña), catálogos, configuración y bitácora. La contraseña se escribe en un formulario y nunca se muestra después.
- **Reportes PDF** \[DECIDIDO\], desde la ficha y para los 3 roles: (1) timeline del instrumento, con encabezado de datos y eventos en orden cronológico; (2) certificados del instrumento en un solo PDF ordenado por fecha de calibración, con portada que lista fecha, número de certificado y próxima fecha, sin los certificados eliminados \[PROPUESTA en el formato combinado\].

## 8. Seguridad

Los controles de 6.10 y los siguientes están implementados y probados; docs/seguridad-checklist.md enlaza cada control con su prueba o comando.

- HTTPS con Caddy: certificado automático con dominio público, o certificado interno en red privada. HSTS solo sobre HTTPS.
- Cabeceras en toda la API: nosniff, Referrer-Policy, frame-ancestors, X-Frame-Options y Cache-Control: no-store (también en los errores). El frontend añade una Content-Security-Policy estricta.
- Contenedores sin root, sin capacidades, con sistema de archivos de solo lectura donde es posible, sin puertos de base de datos hacia el host, con límites de memoria y versiones con digest.
- Secretos solo en variables de entorno, nunca en el repositorio; escaneo con gitleaks. pip-audit y npm audit en la integración continua.
- Certificados nunca servidos como archivos públicos. Datos sensibles fuera de logs.

## 9. Despliegue y operación \[DECIDIDO: servidor propio con Linux; el resto PROPUESTA\]

- **Servidor:** Ubuntu Server o Debian con Docker Engine y Compose; mínimo 2 núcleos y 4 GB de RAM, más disco para la base de datos y los certificados. El contenedor api tiene un límite de 768 MiB (cada verificación Argon2 usa unos 100 MiB; si se suben workers, recalcular).
- **Red:** solo red interna o VPN; en producción se acotan los proxies de confianza a la subred de Docker.
- **Instalación:** docker-compose.yml, un .env con la configuración de la empresa y un script que levanta todo y crea el primer administrador. La empresa debe mantener siempre dos administradores activos y alguien con acceso al servidor.
- **Sin internet:** las imágenes se pueden entregar como archivo. El servidor necesita llegar al servidor de correo.
- **Actualizaciones:** nuevas imágenes; respaldo automático y migraciones al arrancar.
- **Respaldos:** script nocturno de la base de datos y de la carpeta de certificados a una ubicación externa. Es responsabilidad del cliente y va en la guía.

## 10. Plan de trabajo (3 meses, 6 sprints de 2 semanas) \[PROPUESTA\]

| Sprint | Semanas | Entregable |
| --- | --- | --- |
| 1. Cimientos | 1-2 | Repositorio, Compose, Django + DRF, usuario con rol, login y gestión de usuarios, permisos por rol, modelo Evento y lectura de la bitácora, pruebas y revisión de seguridad, base de Next.js con login y navegación, integración continua |
| 2. Catálogos e instrumentos | 3-4 | Catálogos con datos semilla, Instrumento con validación del ID, alta atómica con calibración inicial y certificado PDF, almacenamiento de archivos |
| 3. Estados y eventos | 5-6 | Cambios de estado y de área, calibraciones nuevas con próxima fecha, reemplazos, eventos de instrumentos, timeline |
| 4. Avisos y proceso diario | 7-8 | Avisos, comando proceso\_diario (correos, paso a Caducado, limpieza de sesiones, resumen), contenedor cron |
| 5. Consulta y reportes | 9-10 | Dashboard, calendario, listado con filtros, ficha completa, reportes PDF |
| 6. Cierre | 11-12 | Pruebas de punta a punta, empaquetado, respaldos, imágenes sin conexión, guía de instalación, piloto y correcciones; incluye margen para imprevistos |

## 11. Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Crecimiento del alcance | La sección 2 es el límite; lo demás va a una lista de pendientes |
| Los avisos no salen por la red o el correo de la empresa | Probar SMTP pronto (sprint 4); error guardado en Aviso y resumen del proceso diario |
| El proceso diario deja de correr sin que nadie lo note | Evento PROCESO\_DIARIO\_EJECUTADO y alerta visible en el dashboard si pasó más de un día sin corrida \[PROPUESTA\] |
| Bloqueo de una cuenta por terceros (sin desbloqueo manual) | Aceptado: evento CUENTA\_BLOQUEADA, límite por IP, red interna o VPN; evaluar bloqueo progresivo si resulta un problema |
| El administrador conoce las contraseñas de los usuarios | Aceptado: advertencia en la guía; el evento de restablecimiento deja rastro |
| Intentos legítimos simultáneos con otro contra la misma cuenta reciben 401 | Aceptado: se mide con el motivo intento\_simultaneo; evaluar reservar el intento en el sprint 6 |
| Errores en fechas, zonas horarias y fin de mes | Fechas como DATE, momentos en UTC, pruebas dedicadas |
| Dependencias de WeasyPrint dentro de Docker | Probar la generación de PDF en la imagen final desde el sprint 5 |
| Actualizaciones que dañan datos del cliente | Respaldo automático antes de migrar y migraciones probadas |
| Pérdida de certificados por falta de respaldos | Guía clara, script incluido y huella SHA-256 |
| Que Claude Code se desvíe de lo definido | Este documento como referencia única, pruebas de las reglas de negocio y preguntar ante vacíos |

## 12. Guía para Claude Code

- Lee este documento completo antes de escribir código. Si algo es ambiguo, contradictorio o no está definido, pregunta a Vul; no inventes reglas de negocio. No edites este archivo.
- Trabaja por sprints y no agregues funcionalidad fuera de la sección 2. Antes de añadir una librería de interfaz o un tipo de evento nuevo, pregunta.
- Las reglas de negocio viven en una capa de servicios, no en vistas ni serializadores. Toda operación que cambia datos de negocio escribe su Evento en la misma transacción.
- No hay borrados físicos de registros de negocio. Todo endpoint nuevo declara su permiso y se registra en la matriz de pruebas.
- Pruebas automáticas obligatorias para: formato y unicidad del ID, próxima fecha (incluido fin de mes), calibración vigente por fecha más reciente, alta atómica, caducidad automática (En operación y Fuera de servicio; ignora Baja y Caducado), matriz de permisos por rol, certificado activo único, cancelación de avisos al registrar una calibración y flujo de reemplazo.
- La interfaz está en español. Mantén al día README, .env.example y docs/decisiones.md. Nada de secretos en el repositorio.

## 13. Decisiones abiertas

1. Campos obligatorios y opcionales del instrumento (connection\_pipe, asignado, responsable y frecuencia de verificación).
2. Cantidad de dígitos del número del ID (el ejemplo usa 2).
3. Estado inicial al dar de alta un instrumento (propuesta: elegir entre En operación y Fuera de servicio).
4. Al calibrar un instrumento Caducado: ¿pasa a En operación o regresa a su estado previo? Propuesta: el técnico elige.
5. ¿Se pueden corregir calibraciones ya registradas? Propuesta: no se editan; el administrador corrige con un evento.
6. Configuración del correo (SMTP): variables de entorno o editable desde la interfaz.
7. Correo automático al pasar a Caducado.
8. Confirmar los valores provisionales de la sección 6.10.
9. Tercer reporte (posterior a la v1).
10. Ventana de próximos a vencer (propuesta: 30 días, configurable).

## 14. Cambios respecto a la v1.0

- Modelo de contraseñas: las asigna y restablece solo el administrador; no hay contraseña temporal ni cambio forzado; los técnicos y espectadores no cambian la suya; dos administradores por empresa; sin desbloqueo manual; comando restablecer\_admin.
- Usuario: se agregan numero\_nomina (opcional y único) y creado\_en.
- Evento: se agregan ip e instrumento\_id; se quitan los eventos de avisos y no se registra el cierre de sesión; se agregan USUARIO\_EDITADO, USUARIO\_REACTIVADO, USUARIO\_CONTRASENA\_CAMBIADA y CUENTA\_BLOQUEADA; criterio de pocos eventos.
- Instalación solo en red interna o VPN; límite de memoria del contenedor api; proxies de confianza acotados en producción.
- La limpieza de sesiones se suma al proceso diario; el Evento mínimo, el login y la gestión de usuarios pasan al sprint 1.
- Decisiones abiertas 8, 9 y 10 de la v1.0 resueltas: valores provisionales con variable, el administrador restablece, y dominio en español con código técnico en inglés.
