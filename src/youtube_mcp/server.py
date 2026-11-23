"""youtube-mcp: MCP server for managing a YouTube channel.

Tools cover auth (login/status/logout with automatic token refresh), channel
and video inspection, uploads with metadata, thumbnail setting, updates,
deletion, and playlist management.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from . import auth, client

server = MCPServer(
    name="youtube-mcp",
    version="0.1.0",
    description=(
        "Manage a YouTube channel: OAuth login with automatic token refresh, "
        "video upload/metadata/thumbnails, channel and video inspection, "
        "and playlist management via the YouTube Data API v3."
    ),
)

PrivacyStatus = Literal["private", "public", "unlisted"]


def _video_brief(v: dict[str, Any]) -> dict[str, Any]:
    """Compact video representation for list responses."""
    sn, st = v.get("snippet", {}), v.get("statistics", {})
    return {
        "video_id": v["id"],
        "url": f"https://www.youtube.com/watch?v={v['id']}",
        "title": sn.get("title"),
        "published_at": sn.get("publishedAt"),
        "duration": v.get("contentDetails", {}).get("duration"),
        "privacy": v.get("status", {}).get("privacyStatus"),
        "views": int(st.get("viewCount", 0)),
        "likes": int(st.get("likeCount", 0)),
    }


# ---------------------------------------------------------------- auth tools
@server.tool(
    description=(
        "Authorize this server with the user's Google account (one-time per "
        "account). Opens the browser for OAuth consent, stores the refresh "
        "token locally (0600), and returns the connected channel. Run again "
        "if yt_auth_status reports the token was revoked."
    ),
)
def yt_auth_login(timeout_seconds: int = 180) -> dict[str, Any]:
    """Run the loopback OAuth flow and store the refresh token."""
    return auth.login(timeout_seconds=min(max(timeout_seconds, 30), 600))


@server.tool(
    description=(
        "Check whether the server is authorized and the refresh token still "
        "works (auto-refreshes the access token if expired)."
    ),
)
def yt_auth_status() -> dict[str, Any]:
    """Return auth state, token path, scopes, and any refresh error."""
    status = auth.auth_status()
    if status.get("authenticated"):
        try:
            ch = client.call(
                lambda: client.service().channels().list(part="snippet", mine=True).execute()
            )
            if ch.get("items"):
                status["channel"] = ch["items"][0]["snippet"]["title"]
                status["channel_id"] = ch["items"][0]["id"]
        except Exception as exc:  # auth fine, API read failed
            status["channel_error"] = str(exc)
    return status


@server.tool(description="Remove the stored refresh token (logout). Safe to re-run login later.")
def yt_auth_logout() -> dict[str, Any]:
    """Delete the local token file."""
    return auth.logout()


# ------------------------------------------------------------ channel/video
@server.tool(
    description=(
        "Get the authorized channel's title, ID, description, and statistics "
        "(subscribers, videos, views)."
    ),
)
def yt_get_channel() -> dict[str, Any]:
    """Return snippet + statistics for the logged-in channel."""
    resp = client.call(
        lambda: client.service().channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
    )
    return resp["items"][0]


@server.tool(
    description=(
        "List the channel's uploaded videos, newest first. Returns title, ID, "
        "URL, duration, privacy, publish date, and view/like counts. "
        "Paginate with page_token from the previous response."
    ),
)
def yt_list_videos(page_token: str | None = None, max_results: int = 10) -> dict[str, Any]:
    """List uploads from the authorized channel (max 50 per call)."""
    max_results = min(max(max_results, 1), 50)
    yt = client.service()
    channel = client.call(lambda: yt.channels().list(part="contentDetails", mine=True).execute())
    uploads = channel["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = client.call(
        lambda: yt.playlistItems().list(
            part="snippet,contentDetails", playlistId=uploads,
            maxResults=max_results, pageToken=page_token or "",
        ).execute()
    )
    ids = [i["contentDetails"]["videoId"] for i in items.get("items", [])]
    details = client.video_snippet_stats(ids) if ids else []
    return {
        "videos": [_video_brief(v) for v in details],
        "next_page_token": items.get("nextPageToken"),
    }


@server.tool(
    description=(
        "Get full details for one video: title, description, tags, category, "
        "duration, privacy, embed status, and statistics."
    ),
)
def yt_get_video(video_id: str) -> dict[str, Any]:
    """Return snippet/statistics/status for a video ID."""
    items = client.video_snippet_stats([video_id])
    if not items:
        raise ValueError(f"No video found for id {video_id!r} (private/deleted/wrong ID?).")
    return items[0]


@server.tool(
    description=(
        "Upload a video file to the channel. Requires title; description, "
        "tags, privacy (default private), category, made_for_kids, and "
        "publish_at (RFC3339, only for private) are optional. Resumable "
        "upload; costs 1,600 quota units. Returns the video ID and URL."
    ),
)
def yt_upload_video(
    file_path: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: PrivacyStatus = "private",
    category_id: str | None = None,
    made_for_kids: bool | None = None,
    publish_at: str | None = None,
) -> dict[str, Any]:
    """Upload a local video file with metadata."""
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    snippet: dict[str, Any] = {"title": title, "description": description}
    if tags:
        snippet["tags"] = tags
    if category_id:
        snippet["categoryId"] = category_id
    if made_for_kids is not None:
        snippet["status"] = {"selfDeclaredMadeForKids": made_for_kids}
    status: dict[str, Any] = {"privacyStatus": privacy_status}
    if publish_at:
        if privacy_status != "private":
            raise ValueError("publish_at (scheduled publishing) requires privacy_status='private'.")
        status["publishAt"] = publish_at

    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True)
    request = client.service().videos().insert(
        part="snippet,status", body={"snippet": snippet, "status": status}, media_body=media
    )
    response = None
    while response is None:
        status_code, response = request.next_chunk()
        if status_code:
            pass  # progress available for callers watching logs
    video_id = response["id"]
    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "privacy_status": privacy_status,
        "title": title,
    }


@server.tool(
    description=(
        "Update fields on a video the channel owns: title, description, tags, "
        "privacy, or category. Only provided fields are changed."
    ),
)
def yt_update_video(
    video_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    privacy_status: PrivacyStatus | None = None,
    category_id: str | None = None,
    publish_at: str | None = None,
) -> dict[str, Any]:
    """Patch metadata of an owned video. publish_at (RFC3339, e.g.
    2026-11-22T04:00:00+05:30) reschedules auto-publish; requires the video
    to stay private."""
    body: dict[str, Any] = {}
    snippet = {k: v for k, v in (("title", title), ("description", description),
                                 ("tags", tags), ("categoryId", category_id)) if v is not None}
    if snippet:
        existing = client.video_snippet_stats([video_id])
        if not existing:
            raise ValueError(f"No video found for id {video_id!r}.")
        snippet.setdefault("categoryId", existing[0]["snippet"].get("categoryId", "22"))
        body["snippet"] = snippet
    status: dict[str, Any] = {}
    if privacy_status:
        status["privacyStatus"] = privacy_status
    if publish_at:
        if (privacy_status or "private") != "private":
            raise ValueError("publish_at (scheduled publishing) requires the video to be private.")
        status["publishAt"] = publish_at
    if status:
        body["status"] = status
    if not body:
        raise ValueError("Provide at least one field to update.")
    part = "snippet,status" if "status" in body else "snippet"
    resp = client.call(
        lambda: client.service().videos().update(part=part, body=body).execute()
    )
    return {"video_id": resp["id"], "updated": sorted(body.keys()),
            "publish_at": resp.get("status", {}).get("publishAt")}


@server.tool(
    description=(
        "Set a custom thumbnail image (JPG/PNG, under 2 MB, 16:9) on an owned "
        "video. The channel must be verified for custom thumbnails."
    ),
)
def yt_set_thumbnail(video_id: str, image_path: str) -> dict[str, Any]:
    """Upload a thumbnail image for a video."""
    path = Path(image_path).expanduser()
    if not path.is_file():
        raise ValueError(f"Image not found: {path}")
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(path), mimetype="image/jpeg")
    resp = client.call(
        lambda: client.service().thumbnails().set(videoId=video_id, media_body=media).execute()
    )
    return {"video_id": video_id, "thumbnails": resp.get("items", [{}])[0]}


@server.tool(
    description=(
        "Permanently delete a video from the channel. Destructive and "
        "irreversible — confirm with the user before calling."
    ),
    annotations={"destructiveHint": True, "idempotentHint": True},
)
def yt_delete_video(video_id: str) -> dict[str, Any]:
    """Delete an owned video by ID."""
    client.call(lambda: client.service().videos().delete(id=video_id).execute())
    return {"deleted": video_id}


# --------------------------------------------------------------- playlists
@server.tool(description="List the channel's playlists with title, ID, and item counts.")
def yt_list_playlists(page_token: str | None = None, max_results: int = 10) -> dict[str, Any]:
    """List playlists owned by the authorized channel."""
    yt = client.service()
    resp = client.call(
        lambda: yt.playlists().list(
            part="snippet,contentDetails", mine=True,
            maxResults=min(max(max_results, 1), 50), pageToken=page_token or "",
        ).execute()
    )
    return {
        "playlists": [
            {
                "playlist_id": p["id"],
                "title": p["snippet"]["title"],
                "item_count": p["contentDetails"].get("itemCount", 0),
            }
            for p in resp.get("items", [])
        ],
        "next_page_token": resp.get("nextPageToken"),
    }


@server.tool(
    description="Add a video to a playlist the channel owns.",
    annotations={"destructiveHint": False},
)
def yt_add_video_to_playlist(playlist_id: str, video_id: str) -> dict[str, Any]:
    """Insert a playlist item pointing at video_id."""
    body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    resp = client.call(
        lambda: client.service().playlistItems().insert(part="snippet", body=body).execute()
    )
    return {"playlist_item_id": resp["id"], "playlist_id": playlist_id, "video_id": video_id}


@server.tool(
    description="Remove a video from a playlist (does not delete the video).",
    annotations={"destructiveHint": True, "idempotentHint": True},
)
def yt_remove_video_from_playlist(playlist_id: str, video_id: str) -> dict[str, Any]:
    """Find and delete the playlist item for video_id in playlist_id."""
    yt = client.service()
    resp = client.call(
        lambda: yt.playlistItems().list(part="snippet", playlistId=playlist_id, maxResults=50).execute()
    )
    match = next(
        (i for i in resp.get("items", []) if i["snippet"]["resourceId"].get("videoId") == video_id),
        None,
    )
    if not match:
        raise ValueError(f"{video_id} not found in playlist {playlist_id}.")
    client.call(lambda: yt.playlistItems().delete(id=match["id"]).execute())
    return {"removed": video_id, "playlist_id": playlist_id}


def main() -> None:
    """Entry point for the `youtube-mcp` console script / `python -m youtube_mcp`."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
