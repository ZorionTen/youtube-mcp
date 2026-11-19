"""Thin YouTube Data API v3 wrapper: builds the service, retries after token
refresh, and converts Google API errors into actionable messages."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import auth

T = TypeVar("T")

QUOTA_HINTS = {
    "quotaExceeded": (
        "YouTube API daily quota exceeded (uploads cost 1,600 of the default "
        "10,000 units/day). Wait for the quota reset (midnight Pacific) or "
        "request a quota increase."
    ),
}


def service():
    return build("youtube", "v3", credentials=auth.ensure_credentials(), cache_discovery=False)


def call(fn: Callable[[], T]) -> T:
    """Run a YouTube API callable; on expired access token, refresh and retry once."""
    try:
        return fn()
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        if status in (401, 403):
            # Force a token refresh and retry a single time.
            creds = auth.ensure_credentials()
            creds.token = None
            creds.refresh(Request())
            try:
                return fn()
            except HttpError as retry_exc:
                raise _friendly(retry_exc) from retry_exc
        raise _friendly(exc) from exc


def _friendly(exc: HttpError) -> ValueError:
    status = getattr(exc.resp, "status", None)
    reason = ""
    try:
        body = exc.content.decode(errors="replace")
        if '"reason"' in body:
            import json

            parsed = json.loads(body)
            reason = parsed["error"]["errors"][0].get("reason", "")
    except Exception:
        pass
    if reason in QUOTA_HINTS:
        return ValueError(QUOTA_HINTS[reason])
    if status == 404:
        return ValueError("Video/playlist not found (404). Check the ID — it may be private, deleted, or mistyped.")
    if status == 403:
        return ValueError(
            f"Forbidden (403{', ' + reason if reason else ''}). Check that the video is "
            "owned by the authorized channel and the scope youtube.upload was granted."
        )
    if status == 401:
        return ValueError("Unauthorized (401). Run yt_auth_login to re-authorize.")
    return ValueError(f"YouTube API error {status}: {reason or str(exc)[:200]}")


def uploads_playlist(channel_id: str) -> str:
    resp = call(lambda: service().channels().list(part="contentDetails", id=channel_id).execute())
    return resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]


def video_snippet_stats(video_ids: list[str]) -> list[dict[str, Any]]:
    resp = call(
        lambda: service()
        .videos()
        .list(part="snippet,statistics,contentDetails,status", id=",".join(video_ids))
        .execute()
    )
    return resp.get("items", [])
