"""Cliente HTTP mínimo (solo biblioteca estándar) para las pruebas contra el stack real."""

import http.client
import json
import os
from urllib.parse import urlparse

BASE = urlparse(os.environ["PROXY_BASE_URL"])


class Cliente:
    """Navegador mínimo sobre http.client: guarda cookies y envía el token CSRF.

    `pedir` devuelve (estado, cabeceras en minúsculas, cuerpo JSON). Tras cada llamada,
    `set_cookie` conserva las cabeceras Set-Cookie crudas de esa respuesta."""

    def __init__(self, host=None, puerto=None, cabeceras_fijas=None):
        self.host = host or BASE.hostname
        self.puerto = puerto or BASE.port
        self.cabeceras_fijas = cabeceras_fijas or {}
        self.cookies, self.token, self.set_cookie = {}, None, []

    def pedir(self, metodo, ruta, cuerpo=None, extra=None):
        cabeceras = {**self.cabeceras_fijas, **(extra or {})}
        if self.cookies:
            cabeceras["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if metodo != "GET":
            if self.token is None:
                self.obtener_csrf()
            cabeceras["X-CSRFToken"] = self.token
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo)
            cabeceras["Content-Type"] = "application/json"
        conexion = http.client.HTTPConnection(self.host, self.puerto, timeout=15)
        conexion.request(metodo, ruta, body=datos, headers=cabeceras)
        respuesta = conexion.getresponse()
        crudo = respuesta.read()
        self.set_cookie = respuesta.msg.get_all("Set-Cookie") or []
        for cookie in self.set_cookie:
            nombre, _, valor = cookie.split(";")[0].partition("=")
            if valor:
                self.cookies[nombre] = valor
            else:
                self.cookies.pop(nombre, None)
        conexion.close()
        try:
            cuerpo_json = json.loads(crudo) if crudo else None
        except ValueError:
            cuerpo_json = None  # p. ej. el HTML de un 404 de Django
        return respuesta.status, {k.lower(): v for k, v in respuesta.getheaders()}, cuerpo_json

    def obtener_csrf(self):
        # En producción la cookie CSRF se llama __Host-csrftoken; el token va en el cuerpo.
        _, _, cuerpo = self.pedir("GET", "/api/v1/auth/csrf/")
        self.token = cuerpo["csrfToken"]

    def iniciar_sesion(self, correo, contrasena):
        self.obtener_csrf()
        resultado = self.pedir(
            "POST", "/api/v1/auth/login/", {"correo": correo, "contrasena": contrasena}
        )
        if resultado[0] == 200:
            self.obtener_csrf()  # el token rota al iniciar sesión
        return resultado


def atributos_de(set_cookie: str) -> dict:
    """'n=v; HttpOnly; Max-Age=1800' -> {'nombre': 'n', 'httponly': True, 'max-age': '1800'}"""
    partes = [p.strip() for p in set_cookie.split(";")]
    nombre, _, valor = partes[0].partition("=")
    atributos = {"nombre": nombre, "valor": valor}
    for parte in partes[1:]:
        clave, _, contenido = parte.partition("=")
        atributos[clave.lower()] = contenido or True
    return atributos


def cookies_por_nombre(cabeceras_set_cookie) -> dict:
    return {a["nombre"]: a for a in map(atributos_de, cabeceras_set_cookie)}
