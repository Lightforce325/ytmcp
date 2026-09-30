# Authentication

`ytmcp` uses a **hybrid** auth model. Two providers are supported and one is
selected automatically (unless you pin one explicitly).

```
auth_mode = auto   →  prefer OAuth if configured, else cookies
auth_mode = oauth  →  force OAuth
auth_mode = cookie →  force cookie
```

Set the mode via `YTMCP_AUTH_MODE`.

---

## 1. Cookie authentication

Cookie auth reuses your existing browser session. It is the quickest way to
get started and needs no Google Cloud project.

### Exporting cookies

Pick whichever is easiest:

**a) Browser extension** — install *Get cookies.txt LOCALLY* (Chrome/Firefox),
open `youtube.com`, and export as Netscape format.

**b) yt-dlp** (if installed):

```bash
yt-dlp --cookies-from-browser chrome --cookies cookies.txt \
  --skip-download "https://www.youtube.com"
```

**c) Playwright / JSON** — any JSON export works, including Playwright's
`storage_state.json`.

### Required cookies

For a valid session the jar must contain at least one of:
`SAPISID`, `__Secure-3PAPISID`, or `SID`.

### Configuring

```bash
export YTMCP_AUTH_MODE=cookie
export YTMCP_COOKIE_FILE=/absolute/path/to/cookies.txt
uv run ytmcp auth verify-cookies /absolute/path/to/cookies.txt
```

### Caveats

- Cookies expire; re-export when requests start returning 401/403.
- Google may present a challenge on unfamiliar IPs.
- Never commit cookie files — they are gitignored by default.

---

## 2. OAuth 2.0 (device flow)

Best for headless servers and long-running agents. Tokens auto-refresh.

### Create a client

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **YouTube Data API v3**.
3. Create credentials → **OAuth client ID** → application type
   **TVs and Limited Input devices**.
4. Note the client id and secret.

### Log in

```bash
export YTMCP_AUTH_MODE=oauth
export YTMCP_OAUTH_CLIENT_ID=xxxx.apps.googleusercontent.com
export YTMCP_OAUTH_CLIENT_SECRET=xxxx

uv run ytmcp auth login
# → visit the URL, enter the code, grant access
```

Tokens are cached at `~/.ytmcp/oauth_token.json` (override with
`YTMCP_DATA_DIR`). Subsequent runs refresh automatically.

### Scopes

```
https://www.googleapis.com/auth/youtube
https://www.googleapis.com/auth/youtube.force-ssl
```

---

## 3. Choosing a mode

| Situation | Recommended |
|-----------|-------------|
| Quick local experiments | cookie |
| Long-running agent / server | oauth |
| Both configured | auto (OAuth wins) |
| CI / no interactivity | oauth + pre-seeded refresh token |

Check what will be used:

```bash
uv run ytmcp auth status
uv run ytmcp config      # secrets redacted
```
