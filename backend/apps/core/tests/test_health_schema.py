from unittest import mock

import pytest
from django.db import OperationalError
from django.test import override_settings

pytestmark = pytest.mark.django_db


def test_health_returns_only_status(client):
    response = client.get("/api/v1/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_rejects_other_methods(client):
    assert client.post("/api/v1/health/").status_code == 405


def test_health_reports_error_without_internal_details(client):
    with mock.patch("apps.core.views.connection.cursor", side_effect=OperationalError("secreto")):
        response = client.get("/api/v1/health/")

    assert response.status_code == 503
    assert response.json() == {"status": "error"}
    assert "secreto" not in response.content.decode()


def test_schema_available_in_dev(client):
    response = client.get("/api/v1/schema/")

    assert response.status_code == 200
    assert b"/api/v1/health/" in response.content


@override_settings(OPENAPI_SCHEMA_ENABLED=False)
def test_schema_hidden_when_disabled(client):
    assert client.get("/api/v1/schema/").status_code == 404
