"""OAuth2 login, token storage, and automatic refresh for the YouTube API.

Anyone can use this server with their own Google account:

1. Create OAuth client credentials (Desktop app) in Google Cloud Console.
2. Provide them via env vars or a client_secret.json file (see load_client_credentials).
3. Call the yt_auth_login tool (or run ``python -m youtube_mcp.auth``) once.

The refresh token is stored locally (mode 0600) and renewed automatically on
every API call; a revoked/expired refresh token produces an actionable error
telling the user to re-run login.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
]


class AuthError(RuntimeError):
    """Authentication problem with an actionable fix embedded in the message."""


def config_dir() -> Path:
    base = os.environ.get("YOUTUBE_MCP_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".config", "youtube-mcp"
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_path() -> Path:
    override = os.environ.get("YOUTUBE_MCP_TOKEN_PATH")
    return Path(override) if override else config_dir() / "token.json"


def _read_client_file(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise AuthError(f"Could not read client secrets file {path}: {exc}") from exc
    for key in ("installed", "web"):
        if key in data and "client_id" in data[key]:
            return {
                "client_id": data[key]["client_id"],
                "client_secret": data[key].get("client_secret", ""),
            }
    if "client_id" in data:  # flat format
        return {"client_id": data["client_id"], "client_secret": data.get("client_secret", "")}
    raise AuthError(f"{path} does not contain OAuth client credentials.")


def load_client_credentials() -> dict[str, str]:
    """Resolve OAuth client credentials from env vars or a secrets file."""
    env_id, env_secret = os.environ.get("YOUTUBE_CLIENT_ID"), os.environ.get("YOUTUBE_CLIENT_SECRET")
    if env_id and env_secret:
        return {"client_id": env_id, "client_secret": env_secret}

    candidates = [
        os.environ.get("YOUTUBE_CLIENT_SECRET_FILE"),
        str(Path.cwd() / "client_secret.json"),
        str(config_dir() / "client_secret.json"),
    ]
    for raw in candidates:
        if raw and Path(raw).is_file():
            return _read_client_file(Path(raw))

    raise AuthError(
        "No OAuth client credentials found. Create an OAuth 2.0 client ID "
        "(type: Desktop app) in Google Cloud Console (APIs & Services → "
        "Credentials), then either set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET "
        "or place the downloaded JSON at "
        f"{config_dir() / 'client_secret.json'} (or set YOUTUBE_CLIENT_SECRET_FILE)."
    )


def load_token_store() -> dict[str, Any] | None:
    path = token_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise AuthError(f"Token file {path} is unreadable: {exc}. Re-run yt_auth_login.") from exc


def save_token_store(store: dict[str, Any]) -> Path:
    path = token_path()
    path.write_text(json.dumps(store, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def credentials_from_store(store: dict[str, Any] | None = None) -> Credentials | None:
    """Build Credentials that refresh themselves; None when never logged in."""
    store = store if store is not None else load_token_store()
    if not store or not store.get("refresh_token"):
        return None
    return Credentials(
        token=None,
        refresh_token=store["refresh_token"],
        token_uri=TOKEN_URL,
        client_id=store.get("client_id") or load_client_credentials()["client_id"],
        client_secret=store.get("client_secret") or load_client_credentials()["client_secret"],
        scopes=store.get("scope", " ".join(SCOPES)).split(),
    )


def ensure_credentials() -> Credentials:
    """Return valid credentials, refreshing if needed, or raise AuthError."""
    creds = credentials_from_store()
    if creds is None:
        raise AuthError(
            "Not logged in. Call yt_auth_login (or run: python -m youtube_mcp.auth) "
            "to authorize this server with your Google account."
        )
    try:
        if not creds.valid:
            creds.refresh(Request())
    except Exception as exc:  # refresh_token revoked / network
        raise AuthError(
            f"Token refresh failed ({exc}). The refresh token may be revoked — "
            "run yt_auth_login again to re-authorize."
        ) from exc
    return creds


def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    scope = quote(" ".join(SCOPES))
    return (
        f"{AUTHORIZE_URL}?client_id={quote(client_id)}"
        f"&redirect_uri={quote(redirect_uri)}"
        "&response_type=code"
        f"&scope={scope}"
        f"&state={state}"
        "&access_type=offline&prompt=consent&include_granted_scopes=true"
    )


def _exchange_code(client: dict[str, str], code: str, redirect_uri: str) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    payload = json.dumps(
        {
            "code": code,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise AuthError(f"Code exchange failed ({exc.code}): {detail}") from exc


def login(timeout_seconds: int = 180, open_browser: bool = True) -> dict[str, Any]:
    """Full loopback OAuth flow: serve redirect, open browser, store refresh token."""
    client = load_client_credentials()
    state = secrets.token_urlsafe(16)
    result: dict[str, Any] = {}
    event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            if query.get("state", [""])[0] != state or "code" not in query:
                self.wfile.write(b"OAuth failed - you can close this tab.")
                result["error"] = "State mismatch or missing code."
            else:
                self.wfile.write(b"Authorized - you can close this tab.")
                result["code"] = query["code"][0]
            event.set()

        def log_message(self, *_args):  # silence request logging
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}"
    auth_url = build_auth_url(client["client_id"], redirect_uri, state)

    threading.Thread(target=server.handle_request, daemon=True).start()
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
    if not event.wait(timeout_seconds):
        server.server_close()
        raise AuthError(
            f"No authorization received within {timeout_seconds}s. "
            f"Open this URL manually and try again: {auth_url}"
        )
    server.server_close()
    if "error" in result:
        raise AuthError(f"Authorization failed: {result['error']} Open this URL to retry: {auth_url}")

    tokens = _exchange_code(client, result["code"], redirect_uri)
    if not tokens.get("refresh_token"):
        raise AuthError(
            "Google returned no refresh_token (the account may have previously "
            "approved this app). Revoke access at https://myaccount.google.com/"
            "permissions and run login again."
        )
    store = {
        "refresh_token": tokens["refresh_token"],
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "scope": tokens.get("scope", " ".join(SCOPES)),
        "obtained_at": int(__import__("time").time()),
    }
    path = save_token_store(store)
    creds = credentials_from_store(store)
    creds.refresh(Request())
    account: dict[str, Any] = {}
    try:
        from googleapiclient.discovery import build

        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        ch = yt.channels().list(part="snippet", mine=True).execute()
        if ch.get("items"):
            account = {
                "channel": ch["items"][0]["snippet"]["title"],
                "channel_id": ch["items"][0]["id"],
            }
    except Exception:
        pass
    return {"status": "logged_in", "token_path": str(path), "scopes": store["scope"], **account}


def logout() -> dict[str, Any]:
    path = token_path()
    existed = path.is_file()
    if existed:
        path.unlink()
    return {"status": "logged_out", "removed": existed, "token_path": str(path)}


def auth_status() -> dict[str, Any]:
    store = load_token_store()
    if not store:
        return {"authenticated": False, "hint": "Run yt_auth_login first."}
    creds = credentials_from_store(store)
    try:
        if not creds.valid:
            creds.refresh(Request())
        valid = True
        error = None
    except Exception as exc:
        valid, error = False, str(exc)
    return {
        "authenticated": valid,
        "token_path": str(token_path()),
        "scopes": store.get("scope"),
        "error": error or ("Run yt_auth_login again." if not valid else None),
    }


if __name__ == "__main__":  # standalone login: python -m youtube_mcp.auth
    outcome = login()
    print(json.dumps(outcome, indent=2))
