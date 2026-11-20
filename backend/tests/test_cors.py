"""CORS configuration and middleware behavior tests."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import Settings
from main import configure_cors


def _client_for(settings):
    app = FastAPI()
    app.get("/health")(lambda: {"status": "ok"})
    configure_cors(app, settings)
    return TestClient(app)


def _settings(monkeypatch, origins=None, credentials=None):
    if origins is None:
        monkeypatch.delenv("FLOWPILOT_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("FLOWPILOT_ALLOWED_ORIGINS", origins)
    if credentials is None:
        monkeypatch.delenv("FLOWPILOT_CORS_ALLOW_CREDENTIALS", raising=False)
    else:
        monkeypatch.setenv("FLOWPILOT_CORS_ALLOW_CREDENTIALS", credentials)
    return Settings()


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173", "http://127.0.0.1:5173"],
)
def test_default_localhost_origins_are_allowed(monkeypatch, origin):
    settings = _settings(monkeypatch)
    response = _client_for(settings).get("/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"


def test_configured_production_origin_is_allowed(monkeypatch):
    settings = _settings(monkeypatch, "https://frontend.example.test")
    response = _client_for(settings).get(
        "/health", headers={"Origin": "https://frontend.example.test"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.example.test"


def test_unapproved_origin_does_not_receive_cors_permission(monkeypatch):
    settings = _settings(monkeypatch, "https://frontend.example.test")
    response = _client_for(settings).get(
        "/health", headers={"Origin": "https://unapproved.example.test"}
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_multiple_configured_origins_are_allowed(monkeypatch):
    settings = _settings(
        monkeypatch,
        "https://one.example.test, https://two.example.test",
    )
    client = _client_for(settings)

    for origin in ("https://one.example.test", "https://two.example.test"):
        response = client.get("/health", headers={"Origin": origin})
        assert response.headers["access-control-allow-origin"] == origin


def test_preflight_allows_frontend_post_and_requested_headers(monkeypatch):
    settings = _settings(monkeypatch, "https://frontend.example.test")
    response = _client_for(settings).options(
        "/api/upload",
        headers={
            "Origin": "https://frontend.example.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.example.test"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()


def test_wildcard_with_credentials_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="Wildcard CORS origins"):
        _settings(monkeypatch, "*", "true")


def test_wildcard_without_credentials_is_explicit_and_noncredentialed(monkeypatch):
    settings = _settings(monkeypatch, "*", "false")
    response = _client_for(settings).get(
        "/health", headers={"Origin": "https://any-origin.example.test"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
