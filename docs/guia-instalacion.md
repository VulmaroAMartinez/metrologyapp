# Guía de instalación (borrador del sprint 1)

Se completa en el sprint 6 (empaquetado, respaldos, imágenes sin conexión). Aquí quedan los
requisitos de seguridad que ya son obligatorios.

## Red

El sistema se instala **solo en red interna o VPN**, nunca expuesto directo a internet. El
bloqueo de cuentas y el límite de tasa asumen un atacante con pocas direcciones IP.

## Proxies de confianza (`TRUSTED_PROXY_CIDRS`)

La API toma la IP real del cliente de `X-Forwarded-For` y el esquema (HTTP/HTTPS) de
`X-Forwarded-Proto`, y **solo** si la conexión llega desde una red de `TRUSTED_PROXY_CIDRS`
(el contenedor de Caddy). Desde cualquier otra dirección esas cabeceras se descartan.

El valor por defecto (`.env.example`) abarca todos los rangos privados y sirve para desarrollo.
**`config.settings.prod` se niega a arrancar** si `TRUSTED_PROXY_CIDRS`:

- conserva el valor por defecto,
- está vacío o contiene un CIDR inválido,
- contiene una red más ancha que `/16` (IPv4) o `/64` (IPv6), por ejemplo `10.0.0.0/8`.

### Cómo acotarlo a la subred de Docker

1. Fija la subred de la red interna en el compose de producción, para que no cambie:

   ```yaml
   networks:
     internal:
       ipam:
         config:
           - subnet: 172.28.0.0/24   # elige un rango libre en tu servidor
   ```

2. Pon esa misma subred en el `.env` de producción:

   ```
   TRUSTED_PROXY_CIDRS=172.28.0.0/24
   ```

   Si prefieres no fijarla, consulta la que Docker asignó y úsala:

   ```sh
   docker network inspect <proyecto>_internal --format '{{(index .IPAM.Config 0).Subnet}}'
   ```

   (con una subred automática puede cambiar al recrear la red: vuelve a comprobar tras cada
   cambio de la infraestructura).

3. Si hay un balanceador de la empresa delante de Caddy, ese balanceador va en
   `CADDY_TRUSTED_PROXIES` (para Caddy) y se suma un salto a `API_NUM_PROXIES`; la red de
   Docker sigue siendo la única de `TRUSTED_PROXY_CIDRS`.

`make test-proxy` comprueba este comportamiento contra el stack real: levanta una API en modo
producción con la subred fija `172.29.250.0/24` y confirma que una cabecera falsificada no
sirve para evadir el límite de intentos ni la redirección a HTTPS.
