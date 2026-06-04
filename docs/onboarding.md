# Customer Onboarding Runbook

Step-by-step guide for registering a new YouTube channel (customer) with the jawed service so they can accept live song requests from fans.

---

## Prerequisites

Confirm the following before starting:

| Requirement | Who provides it | Notes |
|-------------|-----------------|-------|
| YouTube channel ID of the streamer | Streamer | Found in YouTube Studio → Channel → Advanced settings |
| Google OAuth `client_id` and `client_secret` | Admin | From the Google Cloud project linked to the jawed app |
| Admin API account on the deployed service | Admin | See [Create an admin user](#1-create-an-admin-user-first-time-only) |
| Deployed service URL | Admin | E.g. `https://<api-id>.execute-api.us-west-2.amazonaws.com/dev` |

---

## Step 1 — Create an admin user (first-time only)

If no admin user exists yet, register one. The first user created becomes the admin.

```bash
curl -X POST "$API_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<strong-password>"}'
```

Save the returned `access_token` — you will need it for all admin operations.

To log in subsequently:

```bash
curl -X POST "$API_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<password>"}'
```

---

## Step 2 — Register the channel

Register the streamer's YouTube channel ID in the service.

```bash
export ADMIN_TOKEN="<access_token from step 1>"

curl -X POST "$API_URL/channels/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{
    "channel_id": "<youtube_channel_id>",
    "channel_name": "<display name, e.g. ReactAttack90>"
  }'
```

This creates:
- An entry in `master.db → channels`
- A per-channel database `data/channel_<channel_id>.db`

---

## Step 3 — Obtain OAuth tokens (manual flow)

> **Note:** A self-service OAuth callback route does not yet exist (see [#4](https://github.com/monkut/youtube-livechat-assistant/issues/4)). Until it is built, tokens must be obtained manually using the Google OAuth Playground or a local script.

### 3a — Required OAuth scopes

```
https://www.googleapis.com/auth/youtube.force-ssl
https://www.googleapis.com/auth/youtube.readonly
```

### 3b — Obtain tokens via Google OAuth Playground

1. Go to [Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground/)
2. Click the settings gear → check "Use your own OAuth credentials"
3. Enter the app's `client_id` and `client_secret`
4. In Step 1, enter the two scopes above and click "Authorize APIs"
5. Sign in as the **channel owner** (the streamer's Google account)
6. Click "Exchange authorization code for tokens"
7. Copy the `access_token`, `refresh_token`, and `expiry` from the response

### 3c — Store tokens in the service

```bash
curl -X PUT "$API_URL/channels/<channel_id>/oauth" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{
    "access_token": "<access_token>",
    "refresh_token": "<refresh_token>",
    "token_expiry": "<expiry ISO 8601, e.g. 2026-06-04T12:00:00+00:00>"
  }'
```

The service will automatically refresh the `access_token` using the `refresh_token` when it expires.

---

## Step 4 — Configure the channel

Before the streamer goes live, set the `live_chat_id` and open the request window.

### 4a — Get the live chat ID

When the streamer starts a live stream, get the `live_chat_id` from the active stream's video ID:

```bash
# Using the YouTube Data API (requires the stored OAuth token to be valid)
curl "https://www.googleapis.com/youtube/v3/videos?part=liveStreamingDetails&id=<video_id>&key=<api_key>"
# Look for: items[0].liveStreamingDetails.activeLiveChatId
```

Alternatively, the streamer can find it in YouTube Studio → Go Live → Chat settings.

### 4b — Open the request window

```bash
curl -X PUT "$API_URL/channels/<channel_id>/config" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{
    "channel_name": "<display name>",
    "live_chat_id": "<live_chat_id>",
    "accepting_requests_start_datetime": "<ISO 8601 UTC, e.g. 2026-06-04T10:00:00+00:00>"
  }'
```

Omitting `accepting_requests_end_datetime` keeps the window open indefinitely. To close:

```bash
curl -X PUT "$API_URL/channels/<channel_id>/config" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{
    "channel_name": "<display name>",
    "accepting_requests_end_datetime": "<ISO 8601 UTC>"
  }'
```

---

## Step 5 — Verify the channel is accepting requests

This endpoint is public — confirm it returns `true` before announcing to fans.

```bash
curl "$API_URL/channels/<channel_id>/accepting-requests"
# Expected: {"channel_id": "...", "accepting_requests": true}
```

---

## Step 6 — Fans submit requests

Fans (or a frontend web form) submit song requests:

```bash
curl -X POST "$API_URL/channels/<channel_id>/requests" \
  -H "Content-Type: application/json" \
  -d '{
    "requesting_username": "FanName",
    "youtube_link": "https://www.youtube.com/watch?v=VIDEO_ID",
    "youtube_link_title": "Artist - Song Title",
    "user_message": "Please react to this!"
  }'
```

If an active `live_chat_id` is configured, jawed automatically posts the request to the live chat.

---

## Streamer go-live checklist

- [ ] Channel registered (Step 2)
- [ ] OAuth tokens stored and valid (Step 3)
- [ ] Stream is live and `live_chat_id` is configured (Step 4a–4b)
- [ ] `accepting_requests` returns `true` (Step 5)
- [ ] Share `POST /channels/<channel_id>/requests` URL with fans or embed in a web form

---

## Closing a stream session

1. Close the request window (Step 4 — set `accepting_requests_end_datetime`)
2. The service will stop accepting new requests immediately

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `400 Channel is not currently accepting requests` | Request window not open | Set `accepting_requests_start_datetime` and clear `accepting_requests_end_datetime` |
| `chat_error: No active live chat found` | `live_chat_id` not set or stream ended | Update `live_chat_id` via Step 4b |
| `Failed to get credentials` | OAuth tokens missing or expired | Re-run Step 3 |
| `401 Invalid or expired token` | Admin JWT expired (24h TTL) | Re-login via `POST /auth/login` |

---

## Open gaps (tracked issues)

- [#4](https://github.com/monkut/youtube-livechat-assistant/issues/4) — Self-service Google OAuth consent screen (replaces manual Step 3)
- [#9](https://github.com/monkut/youtube-livechat-assistant/issues/9) — `/streams/start`, `/streams/end` endpoints (streamline Step 4)
- [#7](https://github.com/monkut/youtube-livechat-assistant/issues/7) — YouTube Data API quota increase (needed for production load)
