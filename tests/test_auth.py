"""Unit tests for youtube_mcp.auth (no network access)."""

import json

import pytest

from youtube_mcp import auth


def test_build_auth_url_contains_required_params():
    url = auth.build_auth_url("cid.apps.googleusercontent.com", "http://localhost:1234", "abc123")
    assert url.startswith(auth.AUTHORIZE_URL)
    assert "client_id=cid.apps.googleusercontent.com" in url
    assert "redirect_uri=http%3A//localhost%3A1234" in url  # quote() keeps "/"
    assert "response_type=code" in url
    assert "state=abc123" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    for scope in auth.SCOPES:
        assert scope.replace(":", "%3A") in url  # quote() keeps "/" unencoded


def test_load_client_credentials_from_env(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id123")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "sec123")
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET_FILE", raising=False)
    assert auth.load_client_credentials() == {"client_id": "id123", "client_secret": "sec123"}


def test_load_client_credentials_from_installed_json(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("YOUTUBE_MCP_CONFIG_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "client_secret.json").write_text(
        json.dumps({"installed": {"client_id": "f-id", "client_secret": "f-sec"}})
    )
    assert auth.load_client_credentials() == {"client_id": "f-id", "client_secret": "f-sec"}


def test_load_client_credentials_missing_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET_FILE", raising=False)
    monkeypatch.setenv("YOUTUBE_MCP_CONFIG_DIR", str(tmp_path / "empty"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(auth.AuthError, match="YOUTUBE_CLIENT_ID"):
        auth.load_client_credentials()


def test_token_store_roundtrip_and_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_MCP_CONFIG_DIR", str(tmp_path))
    store = {"refresh_token": "rt", "client_id": "c", "client_secret": "s", "scope": " ".join(auth.SCOPES)}
    path = auth.save_token_store(store)
    assert path == tmp_path / "token.json"
    assert auth.load_token_store() == store
    if hasattr(path, "stat"):
        assert path.stat().st_mode & 0o777 == 0o600


def test_credentials_from_store_none_when_logged_out(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_MCP_CONFIG_DIR", str(tmp_path))
    assert auth.credentials_from_store(None) is None


def test_ensure_credentials_raises_actionable_error_when_logged_out(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_MCP_CONFIG_DIR", str(tmp_path))
    with pytest.raises(auth.AuthError, match="yt_auth_login"):
        auth.ensure_credentials()
