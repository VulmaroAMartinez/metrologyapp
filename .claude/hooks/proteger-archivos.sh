#!/bin/bash
# PreToolUse (Edit|Write): protege docs/DEFINICION.md, los archivos .env* (menos la plantilla
# .env.example) y cualquier carpeta secrets/. Recibe por stdin el JSON de la herramienta.
# Acepta rutas de Windows (C:\...) y POSIX; ignora mayúsculas, "." y "..".
# FALLA CERRADO: sin jq, con una entrada ilegible o sin ruta, bloquea (código 2) en vez de dejar
# pasar. Pruebas: backend/config/tests/test_hook_proteger_archivos.py

bloquear() {
  echo "$1" >&2
  exit 2
}

command -v jq >/dev/null 2>&1 \
  || bloquear "Hook de archivos protegidos: falta jq, se bloquea por seguridad. Instala jq."

ruta=$(jq -er '(.tool_input.file_path // .tool_input.notebook_path) | select(type == "string" and length > 0)' 2>/dev/null) \
  || bloquear "Hook de archivos protegidos: no se pudo leer la ruta del archivo, se bloquea por seguridad."

ruta=${ruta//$'\r'/}   # jq.exe de Windows termina las líneas con \r\n
ruta=${ruta//\\//}     # C:\a\b -> C:/a/b
ruta=$(printf '%s' "$ruta" | tr '[:upper:]' '[:lower:]')   # Windows no distingue mayúsculas

# Quita "." y resuelve ".." para que docs/x/../DEFINICION.md no esquive el patrón.
segmentos=()
IFS='/' read -ra partes <<< "$ruta"
for parte in "${partes[@]}"; do
  case "$parte" in
    "" | ".") ;;
    "..") [ "${#segmentos[@]}" -gt 0 ] && unset "segmentos[${#segmentos[@]}-1]" ;;
    *) segmentos+=("$parte") ;;
  esac
done

total=${#segmentos[@]}
[ "$total" -gt 0 ] || bloquear "Hook de archivos protegidos: ruta vacía, se bloquea por seguridad."
ultimo=${segmentos[total-1]}
anterior=""
[ "$total" -gt 1 ] && anterior=${segmentos[total-2]}

protegido=0
[ "$ultimo" = "definicion.md" ] && [ "$anterior" = "docs" ] && protegido=1
case "$ultimo" in
  .env.example) ;;
  .env*) protegido=1 ;;
esac
for segmento in "${segmentos[@]}"; do
  [ "$segmento" = "secrets" ] && protegido=1
done

if [ "$protegido" = 1 ]; then
  bloquear "Archivo protegido: pídele a Vul que lo modifique."
fi
exit 0
