#!/bin/sh
# Verifica la configuración de seguridad de los contenedores EN EJECUCIÓN de un proyecto Compose:
# sin root, sin capacidades, no-new-privileges, sistema de archivos de solo lectura, BD sin puerto
# publicado, único puerto del host en 127.0.0.1 y volumen persistente para la base de datos.
# Uso: sh deploy/verificar_contenedores.sh <proyecto>      (p. ej. metrologia)
set -eu
PROYECTO=${1:?Falta el nombre del proyecto Compose}
FALLOS=0

campo() { docker inspect --format "$2" "$PROYECTO-$1-1"; }
ok() { echo "  OK     $1"; }
falla() { echo "  FALLA  $1"; FALLOS=$((FALLOS + 1)); }
verificar() { # descripción, condición (0 = cumple)
  if [ "$2" = "0" ]; then ok "$1"; else falla "$1"; fi
}
contiene() { # texto, patrón -> 0 si el texto lo contiene
  case "$1" in *"$2"*) echo 0 ;; *) echo 1 ;; esac
}

for servicio in db api web proxy; do
  echo "[$servicio]"
  usuario=$(campo "$servicio" '{{.Config.User}}')
  if [ -n "$usuario" ] && [ "$usuario" != "0" ] && [ "$usuario" != "root" ] && [ "${usuario%%:*}" != "0" ]; then
    ok "corre sin root (User=$usuario)"
  else
    falla "corre sin root (User='$usuario')"
  fi
  verificar "elimina todas las capacidades (cap_drop ALL)" \
    "$(contiene "$(campo "$servicio" '{{.HostConfig.CapDrop}}')" ALL)"
  verificar "no-new-privileges" \
    "$(contiene "$(campo "$servicio" '{{.HostConfig.SecurityOpt}}')" no-new-privileges)"
  verificar "sin modo privilegiado" \
    "$([ "$(campo "$servicio" '{{.HostConfig.Privileged}}')" = "false" ] && echo 0 || echo 1)"
  verificar "sin acceso al socket de Docker" \
    "$([ "$(contiene "$(campo "$servicio" '{{json .Mounts}}')" docker.sock)" = "1" ] && echo 0 || echo 1)"
done

echo "[solo lectura]"
for servicio in api web proxy; do
  verificar "$servicio: sistema de archivos de solo lectura" \
    "$([ "$(campo "$servicio" '{{.HostConfig.ReadonlyRootfs}}')" = "true" ] && echo 0 || echo 1)"
done

echo "[puertos publicados en el host]"
for servicio in db api web; do
  verificar "$servicio: ningún puerto publicado" \
    "$([ "$(campo "$servicio" '{{json .HostConfig.PortBindings}}')" = "{}" ] || [ "$(campo "$servicio" '{{json .HostConfig.PortBindings}}')" = "null" ] && echo 0 || echo 1)"
done
enlaces=$(campo proxy '{{json .HostConfig.PortBindings}}')
verificar "proxy: solo 8080/tcp, enlazado a 127.0.0.1" \
  "$(contiene "$enlaces" '"HostIp":"127.0.0.1"')"
verificar "proxy: ningún enlace a 0.0.0.0" \
  "$([ "$(contiene "$enlaces" '"HostIp":"0.0.0.0"')" = "1" ] && [ "$(contiene "$enlaces" '"HostIp":""')" = "1" ] && echo 0 || echo 1)"

echo "[datos y recursos]"
verificar "db: volumen persistente en /var/lib/postgresql" \
  "$(contiene "$(campo db '{{json .Mounts}}')" '"Destination":"/var/lib/postgresql"')"
verificar "api: límite de memoria fijado" \
  "$([ "$(campo api '{{.HostConfig.Memory}}')" -gt 0 ] && echo 0 || echo 1)"

if [ "$FALLOS" -ne 0 ]; then
  echo "$FALLOS comprobaciones de contenedores fallaron" >&2
  exit 1
fi
echo "Contenedores: todas las comprobaciones pasaron"
