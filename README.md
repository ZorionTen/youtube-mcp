# youtube-mcp

MCP server for managing a YouTube channel with the YouTube Data API v3: upload videos, edit metadata, set thumbnails, inspect channel/video stats, and manage playlists — with **OAuth login and automatic token refresh built into the server**, so anyone can connect it to their own Google account.

## Tools

| Tool | Description |
|---|---|
| `yt_auth_login` | One-time OAuth consent (loopback flow); stores refresh token locally (0600) |
| `yt_auth_status` | Check/refresh authorization, shows connected channel |
| `yt_auth_logout` | Remove the stored token |
| `yt_get_channel` | Channel title, ID, description, statistics |
| `yt_list_videos` | Uploaded videos (paginated): title, URL, duration, privacy, views |
| `yt_get_video` | Full details for one video |
| `yt_upload_video` | Resumable upload with title/description/tags/privacy/schedule |
| `yt_update_video` | Patch metadata or privacy of an owned video |
| `yt_set_thumbnail` | Set a custom thumbnail image |
| `yt_delete_video` | Permanently delete a video (destructive) |
| `yt_list_playlists` | List channel playlists |
| `yt_add_video_to_playlist` | Add a video to a playlist |
| `yt_remove_video_from_playlist` | Remove a video from a playlist (keeps the video) |

## Install

```bash
pip install git+https://github.com/ZorionTen/youtube-mcp.git
# or from a clone:
git clone https://github.com/ZorionTen/youtube-mcp.git && cd youtube-mcp
pip install -e .
```

Requires Python 3.10+.

## Google Cloud setup (once, ~3 minutes)

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com)
2. **Enable the API:** APIs & Services → Library → *YouTube Data API v3* → Enable
3. **Configure consent:** APIs & Services → OAuth consent screen → External → fill App name + support email → add test users if in *Testing*
4. **Add scopes:** OAuth consent screen → Data Access → add
   `.../auth/youtube` and `.../auth/youtube.upload`
5. **Create credentials:** APIs & Services → Credentials → Create credentials → *OAuth client ID* → **Desktop app** → download JSON

Provide the credentials to the server in either way:

```bash
# Option A: environment variables
export YOUTUBE_CLIENT_ID="....apps.googleusercontent.com"
export YOUTUBE_CLIENT_SECRET="GOCSPX-..."

# Option B: secrets file (Google's downloaded JSON works as-is)
export YOUTUBE_CLIENT_SECRET_FILE=~/Downloads/client_secret.json
# or drop it at ~/.config/youtube-mcp/client_secret.json
```

Then authorize once:

```bash
youtube-mcp-auth          # or: python -m youtube_mcp.auth
# your browser opens → sign in → Allow
```

The refresh token is saved to `~/.config/youtube-mcp/token.json` (mode `0600`, git-ignored by any sane `.gitignore`). From then on the server **refreshes access tokens automatically** on every call; if the token is revoked, tools return an error telling you to re-run login.

If no desktop browser is detected automatically, set `YOUTUBE_MCP_BROWSER` to a browser binary (it is called with the authorization URL as its first argument), or open the URL the tool prints manually.

## Client configuration

### Claude Code

```bash
claude mcp add youtube -- npx -y youtube-mcp   # after pip install, or:
claude mcp add youtube -- youtube-mcp
```

### OpenCode (`opencode.jsonc`)

```json
{
  "mcp": {
    "youtube": {
      "type": "local",
      "command": ["youtube-mcp"],
      "enabled": true
    }
  }
}
```

## Quota

YouTube Data API v3 default quota is **10,000 units/day**; a video upload costs **1,600 units** (≈6 uploads/day). Reads cost 1 unit each. `yt_auth_status` reports quota errors with guidance when hit.

## Development

```bash
pip install -e .
python -m pytest            # unit tests (no network)
npx @modelcontextprotocol/inspector youtube-mcp   # interactive testing
```

## License

MIT
