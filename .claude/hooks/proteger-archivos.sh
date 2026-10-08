#!/bin/bash
ruta=$(jq -r '.tool_input.file_path // empty')
case "$ruta" in
  */docs/DEFINICION.md|*/.env)
    echo "Archivo protegido: pídele a Vul que lo modifique." >&2
    exit 2 ;;
esac
exit 0