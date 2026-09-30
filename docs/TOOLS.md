# MCP Tools Reference

All 20 tools exposed by the `ytmcp` MCP server. Tools marked **Auth ✅**
require a valid authenticated session (OAuth or cookies).

## Video

### `upload_video` — Auth ✅
Upload a video file to the authenticated channel.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `file_path` | str | — | Absolute path to the video file |
| `title` | str | — | Title (max 100 chars) |
| `description` | str | `""` | Description text |
| `tags` | list[str] | `null` | Keyword tags |
| `visibility` | str | `private` | public / unlisted / private / scheduled |
| `category_id` | str | `22` | YouTube category id |
| `publish_at` | str | `null` | ISO-8601 (for scheduled) |
| `made_for_kids` | bool | `false` | COPPA flag |
| `thumbnail_path` | str | `null` | Custom thumbnail image |

### `update_video_metadata` — Auth ✅
Edit title, description, tags, category, or language of an existing video.

### `set_video_visibility` — Auth ✅
Change visibility to public / unlisted / private / scheduled.

### `delete_video` — Auth ✅
Permanently delete a video.

### `get_video_info`
Fetch title, description, and view count for a video.

### `search_videos`
Search YouTube for videos matching a query.

### `list_channel_videos`
List a channel's uploads, newest first.

## Playlist

### `create_playlist` — Auth ✅
Create a new playlist.

### `list_playlists`
List a channel's playlists.

### `add_video_to_playlist` — Auth ✅
Add a video to a playlist.

### `remove_video_from_playlist` — Auth ✅
Remove a video from a playlist.

### `delete_playlist` — Auth ✅
Delete a playlist.

## Comments

### `list_comments`
List top-level comments on a video.

### `reply_to_comment` — Auth ✅
Post a reply to a comment.

### `moderate_comment` — Auth ✅
Set a comment's state: `published`, `heldForReview`, or `rejected`.

## Channel & Analytics

### `get_channel_info`
Channel metadata (title, description, avatar, banner).

### `get_subscriber_count`
Subscriber count (own channel if no id given).

### `update_channel_branding` — Auth ✅
Update banner, avatar, and profile links.

### `get_analytics` — Auth ✅
Views, watch-time, subscriber gains/losses over a period.

### `get_top_videos`
The channel's most-viewed videos.

---

## Error behaviour

Tools surface errors as text. Common cases:

| Situation | Result |
|-----------|--------|
| No auth configured, auth required | `AuthRequiredError` message |
| Cookies expired / rejected | `AuthError` (re-export cookies) |
| Rate limited (HTTP 429) | retried, then `RateLimitError` |
| Resource missing | `NotFoundError` |
| Upstream 5xx after retries | `UpstreamError` |
