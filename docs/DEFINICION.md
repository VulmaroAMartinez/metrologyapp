# Sistema de control de instrumentos de metrología

**Definición del proyecto · v1.0 · 7 de octubre de 2026** Equipo: Vul + Claude Code · Plazo de la primera versión: 3 meses

## 0. Cómo leer este documento

- **\[DECIDIDO\]**: lo confirmó Vul. **\[PROPUESTA\]**: recomendación técnica todavía sin confirmar; puede cambiar.
- La sección 13 reúne las decisiones abiertas. Claude Code no debe inventar reglas de negocio: si algo no está definido, consulta esa sección y pregunta a Vul.
- Lo que no esté en el alcance de la sección 2 queda fuera de la v1.

## 1. Resumen

Sistema web para el área de metrología que **reemplaza el Excel** con el que hoy se controlan los instrumentos. Registra instrumentos y su historial de calibraciones, guarda el certificado PDF de cada calibración, calcula fechas de caducidad, envía avisos por correo y mantiene una bitácora de auditoría. \[DECIDIDO\]

## 2. Alcance

**Dentro de la v1:**

- Usuarios con 3 roles; los gestiona el administrador. \[DECIDIDO\]
- Catálogos: áreas, tipos de instrumento, magnitudes y unidades. \[DECIDIDO\]
- Instrumentos: alta (con calibración inicial y certificado), edición, cambio de estado y de área, baja. \[DECIDIDO\]
- Calibraciones y certificados PDF, con eliminación lógica de certificados. \[DECIDIDO\]
- Reemplazos (instrumento de respaldo). \[DECIDIDO\]
- Avisos por correo con fechas manuales por instrumento y paso automático a Caducado. \[DECIDIDO\]
- Timeline por instrumento y bitácora de auditoría. \[DECIDIDO\]
- Dashboard, calendario y listado con filtros. \[DECIDIDO\]
- Reportes PDF: timeline del instrumento y certificados del instrumento. \[DECIDIDO\]
- Instalación con Docker Compose, respaldos y guía de instalación. \[PROPUESTA\]


## 3. Usuarios y permisos

| Acción | Administrador | Técnico | Espectador |
| --- | --- | --- | --- |
| Consultar instrumentos, calibraciones, certificados, timeline, dashboard, calendario y reportes | Sí | Sí | Sí |
| Crear y editar instrumentos, cambiar estado o área, dar de baja, registrar calibraciones, subir certificados, gestionar reemplazos | Sí | Sí | No |
| Crear, editar y cancelar avisos | Sí | Sí | No |
| Eliminar certificados (con motivo) | Sí | No | No |
| Ver la bitácora de auditoría completa | Sí | No | No |
| Gestionar usuarios | Sí | No | No |
| Gestionar catálogos y configuración \[PROPUESTA\] | Sí | No | No |
| Recibir correos de caducidad | Sí | Sí | No |

Los permisos se verifican siempre en el backend; ocultar botones en el frontend no basta. \[DECIDIDO salvo las filas marcadas\]

## 4. Stack y arquitectura

| Capa | Tecnología |
| --- | --- |
| Frontend | Next.js (App Router, TypeScript) \[DECIDIDO\] |
| Backend | Django + Django REST Framework \[DECIDIDO\] |
| Base de datos | PostgreSQL \[DECIDIDO\] |
| API | REST bajo /api/v1, esquema OpenAPI con drf-spectacular y cliente TypeScript generado con openapi-typescript \[DECIDIDO\] |
| Interfaz | Tailwind + shadcn/ui, TanStack Table, FullCalendar \[DECIDIDO\] |
| Autenticación | Sesión con cookie HttpOnly y modelo de usuario propio con rol \[PROPUESTA\] |
| Correo | send\_mail de Django con SMTP configurado por variables de entorno \[PROPUESTA\] |
| Proceso diario | Comando manage.py proceso\_diario ejecutado por cron en un contenedor aparte \[PROPUESTA\] |
| Reportes PDF | WeasyPrint para el timeline y pypdf para unir certificados \[PROPUESTA\] |
| Despliegue | Docker Compose con web (Next.js), api (Django + gunicorn), db, cron y proxy (Caddy) \[PROPUESTA\] |

Next.js y Django van detrás del mismo dominio: /api va a Django y el resto a Next.js. Así las cookies son del mismo origen y se evitan los problemas de CORS. \[PROPUESTA\]

Estructura del repositorio \[PROPUESTA\]:

```
backend/   apps Django: accounts, catalogs, instruments, calibrations, notifications, audit, reports
frontend/  Next.js
deploy/    docker-compose.yml, Caddyfile, .env.example, scripts de instalación y respaldo
docs/      este documento
```

## 5. Modelo de datos

Reglas generales \[PROPUESTA\]: nunca se borra físicamente un registro de negocio; las fechas de calendario son DATE y los momentos son datetime en UTC; la zona horaria local se configura por instalación (America/Mexico\_City por defecto).

| Tabla | Campos | Restricciones |
| --- | --- | --- |
| Usuario | nombre, numero_nomina, debe_cambiar_contrasena, correo, rol (ADMIN, TECNICO, ESPECTADOR), activo, contraseña (hash), intentos\_fallidos, bloqueado\_hasta | correo único; modelo de usuario propio de Django desde el primer commit |
| Area | codigo (3 dígitos), nombre, activa | codigo único |
| TipoInstrumento | codigo (2 letras), nombre, activo | codigo único |
| Magnitud | nombre, activa | nombre único |
| Unidad | magnitud, simbolo, nombre, activa | único (magnitud, simbolo); si está en uso solo se desactiva |
| Instrumento | id interno inmutable; codigo (ID de negocio); area\_origen, linea (A a L o Z), numero, tipo; area\_actual; asignado (texto: máquina, persona, etc.); marca; modelo; numero\_serie; connection\_pipe (texto: código de la tubería a la que está conectado); magnitud; unidad; alcance\_min; alcance\_max; resolucion; error\_maximo\_permitido (en la unidad del instrumento); frecuencia\_calibracion\_meses (entero mayor que 0); frecuencia\_verificacion (texto); responsable\_verificacion (texto); estado; creado\_por; creado\_en | codigo único; (area\_origen, linea, numero, tipo) único; alcance\_min no mayor que alcance\_max; la unidad debe pertenecer a la magnitud |
| Calibracion | instrumento, numero\_certificado, fecha\_calibracion (DATE), proxima\_fecha (DATE, calculada), registrada\_por, registrada\_en | proxima\_fecha no se acepta por la API; la vigente es la de mayor fecha\_calibracion |
| Certificado | calibracion, archivo, nombre\_original, tamano\_bytes, sha256, subido\_por, subido\_en, eliminado\_en, eliminado\_por, motivo\_eliminacion | un solo certificado activo (eliminado\_en nulo) por calibración, con índice único parcial |
| Reemplazo | reemplazante, reemplazado, desde, hasta, creado\_por, cerrado\_por | a lo sumo un reemplazo abierto por reemplazado y por reemplazante |
| Aviso | instrumento, fecha\_envio (DATE), creado\_por, creado\_en, enviado\_en, cancelado\_en, cancelado\_por, error | editable mientras enviado\_en y cancelado\_en sean nulos |
| Evento | ocurrido\_en (UTC), usuario (nulo = Sistema), instrumento (opcional), tipo, descripcion, antes (JSON), despues (JSON) | solo inserción |
| Configuracion | clave, valor | ventana de próximos a vencer (30 días), tamaño máximo de PDF (10 MB) |

### 5.1 Datos semilla \[DECIDIDO para áreas y tipos; PROPUESTA para magnitudes y unidades\]

**Áreas:** Frick 000, Descongelado 050, Eviscerado 100, Cocimiento 150, Spray Room 200, Chill Room 250, Limpieza de atún 300, Calidad 350, Enlatado 400, I + D 450, CMA 500, Tacore 550, Esterilizado 600, Etiquetado 650, Almacén de químicos 700, Calderas 750, Mantenimiento 800, Cuarto de máquinas 850, PETAR 900, Harinera 950.

**Tipos de instrumento:** Indicador de temperatura TI, Transmisor de temperatura TT, Indicador de presión PI, Transmisor de presión PT, Indicador de flujo FI, Transmisor de flujo FT, Indicador de peso WI, Transmisor de peso WT, Indicador de dimensión DI, Transmisor de dimensión DT, Indicador de volumen VI, Transmisor de volumen VT.

**Magnitudes y unidades iniciales:** longitud (mm, m, in), masa (g, kg, lb), tiempo (s, min, h), temperatura (°C, °F, K), presión (bar, psi, kPa, MPa), flujo (L/min, m³/h), volumen (L, m³). El administrador puede agregar más.

Las áreas y los tipos corresponden a la primera empresa; cada instalación puede tener los suyos, por eso son catálogos y no valores fijos en el código.

## 6. Reglas de negocio

### 6.1 ID del instrumento \[DECIDIDO\]

Formato: **Área (3 dígitos) + Línea (A a L o Z) + Número (manual) + Tipo (2 letras)**. Ejemplo: 650C01WI = Etiquetado, línea C, número 01, indicador de peso.

- El ID completo es único en toda la base de datos y se valida contra el formato y los catálogos.
- El ID **nunca cambia**, ni siquiera al mover el instrumento de área. El área actual es un campo aparte, y los filtros por área usan area\_actual, nunca el prefijo del ID.
- Se guarda desglosado (area\_origen, linea, numero, tipo) y armado (codigo).
- Al capturar, el sistema puede sugerir el siguiente número libre. \[PROPUESTA\]

### 6.2 Estados \[DECIDIDO\]

En operación, Fuera de servicio, Baja y Caducado.

| De | A | Cómo ocurre |
| --- | --- | --- |
| En operación | Fuera de servicio | Manual (avería o envío a calibrar) |
| Fuera de servicio | En operación | Manual (reparado, calibrado o asignado como reemplazo) |
| En operación o Fuera de servicio | Caducado | Automático, por el proceso diario (6.5) |
| Caducado | En operación | Al registrar una nueva calibración (ver decisión abierta 4) |
| Cualquiera | Baja | Manual |

**Baja es final**: no se puede revertir, pide confirmación y conserva todo el historial y los certificados. \[DECIDIDO\]

### 6.3 Alta de instrumento \[DECIDIDO\]

El alta es **atómica**: en una sola transacción se crean el instrumento, su calibración inicial y su certificado PDF. No se puede registrar un instrumento sin calibración, así que todo instrumento tiene siempre al menos una calibración y por tanto una próxima fecha. Si algo falla, no se guarda nada.

### 6.4 Calibraciones y fechas \[DECIDIDO\]

- La próxima fecha se calcula sola: **fecha de calibración + frecuencia de calibración en meses** (usar aritmética de meses que respete fin de mes, por ejemplo dateutil.relativedelta: 31 de enero + 1 mes = 28 de febrero).
- La próxima fecha se guarda en la calibración como foto de ese momento y no se edita a mano.
- La calibración vigente es la de mayor fecha\_calibracion, no la última subida.
- Si cambia la frecuencia de un instrumento, aplica desde su siguiente calibración; la próxima fecha ya calculada no se mueve. \[PROPUESTA\]
- Cada nueva calibración exige su certificado PDF.
- Un instrumento sigue vigente durante el día de su próxima fecha y caduca al día siguiente. \[PROPUESTA\]

### 6.5 Caducidad automática \[DECIDIDO\]

El proceso diario pasa a **Caducado** todo instrumento En operación **o Fuera de servicio** (los de respaldo también requieren calibración) cuya próxima fecha vigente sea anterior a la fecha local de hoy. Ignora los instrumentos en Baja y los que ya están Caducados. Cada cambio genera un evento con usuario Sistema.

### 6.6 Reemplazos (instrumento de respaldo) \[DECIDIDO\]

Se basa en el diagrama de flujo de Vul. A es el instrumento en uso y B su respaldo, que se da de alta como Fuera de servicio.

1. A pasa a Fuera de servicio (avería o envío a calibrar).
2. El técnico asigna a B como reemplazo de A: B toma el área actual y el asignado de A, pasa a En operación y se crea un Reemplazo abierto. Si B tiene la calibración vencida, el sistema advierte antes de permitirlo. \[PROPUESTA\]
3. Cuando A regresa (reparado o recién calibrado; si fue calibración se registra con su certificado), A pasa a En operación y B regresa a Fuera de servicio, con su área y asignado previos. Se cierra el Reemplazo. \[PROPUESTA en lo relativo a datos previos\]

Corrección del diagrama: el evento de salida de servicio del paso 3 debe decir **Instrumento B Fuera de servicio** (el diagrama decía A por error). En general, cada evento nombra al instrumento al que le ocurre.

### 6.7 Certificados \[DECIDIDO\]

- Un solo certificado, en PDF, por calibración.
- El sistema valida el contenido del archivo (que empiece como PDF) y no solo la extensión; tamaño máximo configurable, 10 MB por defecto; se guarda su huella SHA-256. \[PROPUESTA\]
- Archivos en una carpeta persistente del servidor (volumen de Docker) con nombres aleatorios. La descarga pasa siempre por un endpoint autenticado, nunca por una ruta pública. \[PROPUESTA\]
- Solo el administrador puede eliminar un certificado, y siempre con motivo. La eliminación es **lógica**: el archivo se conserva, la calibración queda marcada como sin certificado y se puede subir el correcto. El evento guarda quién, cuándo y por qué.

### 6.8 Avisos por correo \[DECIDIDO salvo indicación\]

- Las fechas de aviso se **capturan a mano por instrumento**; cada una es un registro en Aviso. La automatización queda para después, y debe poder funcionar solo generando registros de Aviso.
- Los pueden crear, editar y cancelar el administrador y los técnicos mientras no se hayan enviado.
- Los reciben **todos los técnicos y administradores activos**.
- El proceso diario envía los avisos con fecha de envío menor o igual a hoy que no estén enviados ni cancelados, y registra el resultado.
- Al registrar una nueva calibración se **cancelan los avisos pendientes** del ciclo anterior.
- Al pasar un instrumento a Caducado se envía un correo automático. Si un envío falla, se registra AVISO\_FALLIDO y se reintenta en la siguiente corrida. \[PROPUESTA\]

### 6.9 Eventos: timeline y bitácora \[DECIDIDO\]

Cada evento guarda fecha y hora, usuario (o Sistema), instrumento si aplica, tipo, descripción legible y los datos antes y después. Formato de la descripción: `dd-mm-aaaa hh:mm · ID · texto · usuario`.

Tipos: INSTRUMENTO\_ALTA, INSTRUMENTO\_EDITADO, INSTRUMENTO\_CAMBIO\_AREA, INSTRUMENTO\_CAMBIO\_ESTADO (de → a, con motivo), CALIBRACION\_REGISTRADA, CERTIFICADO\_SUBIDO, CERTIFICADO\_ELIMINADO, REEMPLAZO\_INICIADO, REEMPLAZO\_TERMINADO, AVISO\_CREADO, AVISO\_EDITADO, AVISO\_CANCELADO, AVISO\_ENVIADO, AVISO\_FALLIDO, USUARIO\_CREADO, USUARIO\_ROL\_CAMBIADO, USUARIO\_DESACTIVADO, USUARIO\_CONTRASENA\_RESTABLECIDA, SESION\_INICIADA, SESION\_FALLIDA, CATALOGO\_MODIFICADO, CONFIGURACION\_CAMBIADA, PROCESO\_DIARIO\_EJECUTADO, USUARIO\_CONTRASENA_CAMBIADA, y CUENTA_BLOQUEADA (resumen de avisos enviados e instrumentos pasados a Caducado).

- Solo se registran **cambios**; no se registran consultas ni descargas.
- Los cambios automáticos también generan eventos, con usuario Sistema.
- **Timeline del instrumento** (visible para los 3 roles): eventos INSTRUMENTO\_*, CALIBRACION\_*, CERTIFICADO\_*, REEMPLAZO\_* y AVISO\_ENVIADO de ese instrumento. **Bitácora** (solo administrador): todos los eventos, con filtros por fecha, usuario, instrumento y tipo.
- El evento se escribe en la misma transacción que el cambio; si el evento falla, el cambio se revierte. \[PROPUESTA\]
- La tabla es solo de inserción: el usuario de base de datos de la aplicación no tiene permiso de UPDATE ni DELETE sobre ella. \[PROPUESTA\]

## 7. Pantallas y reportes

- **Dashboard de inicio** \[DECIDIDO\]: cantidad de instrumentos por estado, cuántos caducan en los próximos 30 días (configurable), lista de Caducados y gráfica por área \[PROPUESTA en el detalle\].
- **Calendario** \[DECIDIDO\]: vista mensual, semanal y de lista. Cada instrumento aparece una vez, en el día de su próxima fecha vigente; futuros, próximos a vencer y Caducados con colores distintos. Clic abre la ficha. Filtros por área, tipo y estado. Sin instrumentos en Baja. \[PROPUESTA en el detalle\]
- **Listado de instrumentos** \[PROPUESTA\]: búsqueda, filtros por área actual, tipo, magnitud, estado y rango de próxima fecha, y orden por columna.
- **Ficha del instrumento** \[PROPUESTA\]: datos, historial de calibraciones con descarga del PDF, avisos y timeline.
- **Alta y edición** \[PROPUESTA\]: formulario del instrumento con su calibración inicial en la misma pantalla.
- **Administración** (solo administrador) \[PROPUESTA\]: usuarios, catálogos, configuración y bitácora.
- **Reportes en PDF** \[DECIDIDO\], generados desde la ficha y disponibles para los 3 roles: (1) timeline del instrumento, con encabezado de datos y eventos en orden cronológico; (2) certificados del instrumento, en un solo PDF ordenado por fecha de calibración, con portada que lista fecha, número de certificado y próxima fecha de cada calibración, sin incluir los certificados eliminados \[PROPUESTA en el formato combinado\].

## 8. Seguridad \[PROPUESTA\]

- HTTPS con Caddy: certificado automático si hay dominio público, o certificado interno si solo se usa dentro de la red de la empresa.
- Contraseñas con hash seguro (Argon2), política mínima de longitud, bloqueo temporal tras varios intentos fallidos y límite de intentos de inicio de sesión. Los intentos fallidos van a la bitácora.
- Cookies de sesión Secure, HttpOnly y SameSite; cierre de sesión por inactividad.
- Permisos verificados en el backend con la matriz de la sección 3.
- Certificados nunca servidos como archivos públicos.
- Secretos solo en variables de entorno; nunca en el repositorio.

## 9. Despliegue y operación \[DECIDIDO: servidor propio de la empresa con Linux; el resto PROPUESTA\]

- **Servidor:** Ubuntu Server o Debian con Docker Engine y Compose; mínimo 2 núcleos y 4 GB de RAM, más disco para la base de datos y los certificados.
- **Instalación:** paquete con docker-compose.yml, un .env con la configuración de la empresa (dominio, correo, contraseñas) y un script que levanta todo y crea el primer administrador.
- **Sin internet:** las imágenes de Docker se pueden entregar como archivo para cargarlas sin conexión. El servidor sí necesita llegar al servidor de correo de la empresa.
- **Actualizaciones:** nuevas imágenes; las migraciones se aplican solas al arrancar, después de un respaldo automático.
- **Respaldos:** script nocturno que copia la base de datos y la carpeta de certificados a una ubicación externa (disco o carpeta de red), conservando las últimas copias. Es responsabilidad del cliente y se documenta en la guía.
- **Zona horaria:** configurable por instalación; los eventos se guardan en UTC.

## 10. Plan de trabajo (3 meses, 6 sprints de 2 semanas) \[PROPUESTA\]

| Sprint | Semanas | Entregable |
| --- | --- | --- |
| 1. Cimientos | 1-2 | Repositorio, Docker Compose, Django + DRF, PostgreSQL, usuario con rol, login por sesión, base de Next.js con layout y proxy, interfaz en español, CI básico |
| 2. Catálogos e instrumentos | 3-4 | Catálogos (áreas, tipos, magnitudes, unidades) con datos semilla, modelo de Instrumento con validación del ID, alta atómica con calibración inicial y certificado PDF, almacenamiento de archivos |
| 3. Estados y eventos | 5-6 | Cambios de estado y de área, calibraciones nuevas con cálculo de próxima fecha, reemplazos, tabla de Evento escrita en cada operación, timeline |
| 4. Avisos y proceso diario | 7-8 | CRUD de avisos, comando proceso\_diario (envío de correos, paso automático a Caducado, resumen de corrida), contenedor cron |
| 5. Consulta y reportes | 9-10 | Dashboard, calendario, listado con filtros, ficha completa, vista de bitácora, reportes PDF (timeline y certificados combinados) |
| 6. Cierre | 11-12 | Seguridad (bloqueos, cookies, límites), pruebas de punta a punta, empaquetado de instalación, respaldos, imágenes sin conexión, guía de instalación, piloto y correcciones. Incluye el margen para imprevistos |

Cada sprint termina con algo que se puede probar de punta a punta en un entorno de desarrollo.

## 11. Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Crecimiento del alcance durante el desarrollo | La sección 2 es el límite; todo lo demás va a una lista de pendientes para después de la v1 |
| Los avisos no salen por la red o el correo de la empresa | Probar SMTP pronto (sprint 4), registrar AVISO\_FALLIDO y mostrar el estado de los envíos |
| El proceso diario deja de correr sin que nadie lo note | Evento PROCESO\_DIARIO\_EJECUTADO en cada corrida y alerta visible en el dashboard si pasó más de un día sin corrida \[PROPUESTA\] |
| Errores en fechas, zonas horarias y fin de mes | Fechas de calendario como DATE, momentos en UTC, pruebas dedicadas |
| Dependencias de WeasyPrint dentro de Docker | Probar la generación de PDF en la imagen final desde el sprint 5 |
| Actualizaciones que dañan datos del cliente | Respaldo automático antes de migrar y migraciones probadas con datos de ejemplo |
| Pérdida de certificados por falta de respaldos | Guía clara, script de respaldo incluido y huella SHA-256 para detectar alteraciones |
| Que Claude Code se desvíe de lo definido | Este documento como referencia única, pruebas automáticas de las reglas de negocio y preguntar ante vacíos |

## 12. Guía para Claude Code

- Lee este documento completo antes de escribir código. Si algo es ambiguo, contradictorio o no está definido, pregunta a Vul; no inventes reglas de negocio.
- Trabaja por sprints (sección 10) y no agregues funcionalidad fuera de la sección 2.
- Las reglas de negocio viven en una capa de servicios, no en vistas ni serializadores. Toda operación que cambia datos de negocio escribe su Evento en la misma transacción.
- No hay borrados físicos de registros de negocio.
- Pruebas automáticas obligatorias para: formato y unicidad del ID, cálculo de la próxima fecha (incluido fin de mes), calibración vigente por fecha más reciente, alta atómica, caducidad automática (aplica a En operación y Fuera de servicio, ignora Baja y Caducado), matriz de permisos por rol, certificado activo único, cancelación de avisos al registrar nueva calibración y flujo de reemplazo.
- La interfaz está en español. Mantén al día el README y .env.example. Nada de secretos en el repositorio.

## 13. Decisiones abiertas

1. Campos obligatorios y opcionales del instrumento (por ejemplo connection\_pipe, asignado, responsable y frecuencia de verificación).
2. Cantidad de dígitos del número del ID (el ejemplo usa 2).
3. Estado inicial al dar de alta un instrumento (el diagrama sugiere que A entra En operación y el respaldo B entra Fuera de servicio; se propone permitir elegir entre esos dos).
4. Al registrar una calibración nueva en un instrumento Caducado: ¿pasa a En operación o regresa al estado que tenía? Propuesta: el técnico elige el estado destino.
5. ¿Se pueden corregir calibraciones ya registradas? Propuesta: no se editan; el administrador corrige con un evento que lo documente.
6. Configuración del correo (SMTP): variables de entorno o editable desde la interfaz.
7. Correo automático al pasar a Caducado y reintento de envíos fallidos (propuestos, sin confirmar).
8. Política de contraseñas, tiempo de bloqueo y tiempo de inactividad (valores).
9. Restablecimiento de contraseña: propuesta, lo hace el administrador.
10. Idioma de los identificadores del código (propuesta: inglés en el código y español en la interfaz).
11. Tercer reporte (posterior a la v1).
12. Ventana de próximos a vencer (propuesta: 30 días, configurable).
