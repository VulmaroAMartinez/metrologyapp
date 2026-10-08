import json
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from rest_framework.request import Request
from rest_framework.settings import api_settings

from apps.core.middleware import FORWARDED_META_KEYS, TrustedProxyHeadersMiddleware
from apps.core.network import client_ip

BACKEND_DIR = Path(__file__).resolve().parents[3]
FORGED = {
    "HTTP_X_FORWARDED_FOR": "1.2.3.4",
    "HTTP_X_FORWARDED_PROTO": "https",
    "HTTP_X_FORWARDED_HOST": "evil.example",
    "HTTP_X_FORWARDED_PORT": "443",
    "HTTP_FORWARDED": "for=1.2.3.4;proto=https",
    "HTTP_X_REAL_IP": "1.2.3.4",
}


def run_middleware(remote_addr, **extra):
    seen = {}

    def view(request):
        seen.update(request.META)
        return HttpResponse()

    request = RequestFactory().get("/", REMOTE_ADDR=remote_addr, **extra)
    TrustedProxyHeadersMiddleware(view)(request)
    return seen


@pytest.mark.parametrize("remote", ["8.8.8.8", "203.0.113.9", "2001:db8::1", "no-es-una-ip", ""])
def test_forwarded_headers_are_dropped_from_untrusted_sources(remote):
    seen = run_middleware(remote, **FORGED)

    assert not any(key in seen for key in FORWARDED_META_KEYS)
    assert set(FORGED) == set(FORWARDED_META_KEYS)


@pytest.mark.parametrize("remote", ["127.0.0.1", "172.18.0.5", "10.1.2.3", "::ffff:172.18.0.5"])
def test_forwarded_headers_are_kept_from_the_trusted_proxy(remote):
    seen = run_middleware(remote, **FORGED)

    assert seen["HTTP_X_FORWARDED_FOR"] == "1.2.3.4"
    assert seen["HTTP_X_FORWARDED_PROTO"] == "https"


@override_settings(TRUSTED_PROXY_CIDRS=["172.28.0.0/24", "  "])
def test_trusted_networks_come_from_settings():
    assert "HTTP_X_FORWARDED_FOR" in run_middleware("172.28.0.9", **FORGED)
    assert "HTTP_X_FORWARDED_FOR" not in run_middleware("172.18.0.9", **FORGED)


def drf_request(remote_addr, forwarded_for=None):
    extra = {"HTTP_X_FORWARDED_FOR": forwarded_for} if forwarded_for else {}
    seen = run_middleware(remote_addr, **extra)
    wsgi = RequestFactory().get("/")
    wsgi.META = seen
    return Request(wsgi)


def test_num_proxies_is_configured_for_drf():
    assert api_settings.NUM_PROXIES == 1


def test_client_ip_uses_the_address_appended_by_the_proxy():
    # Caddy agrega la IP que ve al final de X-Forwarded-For; lo anterior lo puso el cliente.
    assert client_ip(drf_request("172.18.0.5", "6.6.6.6, 198.51.100.7")) == "198.51.100.7"


def test_client_ip_ignores_forged_header_from_untrusted_source():
    assert client_ip(drf_request("8.8.8.8", "6.6.6.6")) == "8.8.8.8"


# --- la caché compartida no puede ser local por proceso -----------------------------------

LOCAL_BACKENDS = ("locmem", "dummy", "filebased", "memcached")


def cache_backend_of(module):
    env = {
        "DJANGO_SETTINGS_MODULE": module,
        "DJANGO_SECRET_KEY": "k" * 64,
        "DJANGO_ALLOWED_HOSTS": "metrologia.example.com",
        "DB_NAME": "x",
        "DB_USER": "app",
        "DB_PASSWORD": "una-clave-real",
    }
    code = "from django.conf import settings; import json; print(json.dumps(settings.CACHES))"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["default"]["BACKEND"]


@pytest.mark.parametrize("module", ["config.settings.prod", "config.settings.dev"])
def test_cache_is_shared_database_cache_not_per_process(module):
    backend = cache_backend_of(module)

    assert backend == "django.core.cache.backends.db.DatabaseCache"
    assert not any(local in backend for local in LOCAL_BACKENDS)


def test_test_settings_use_the_same_shared_cache():
    assert settings.CACHES["default"]["BACKEND"].endswith("db.DatabaseCache")
