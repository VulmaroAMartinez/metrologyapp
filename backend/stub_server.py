"""TEMPORAL: responde /api/v1/health/ hasta que exista Django (commit 3)."""

from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok","stub":true}')


HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
