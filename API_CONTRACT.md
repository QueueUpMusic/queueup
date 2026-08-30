# QueueUp API Contract v1

This document defines the `/api/v1/` REST API contract for QueueUp and the deployment architecture for the frontend/backend split.

---

## Base URL

```
/api/v1/
```

All endpoints are relative to this base. The API uses Django session authentication via cookies.

---

## Authentication and Authorization

### Session Authentication
- Django session cookie-based authentication (standard `sessionid` cookie)
- CSRF token required for all state-changing requests (POST, PATCH, DELETE)
- Session middleware must be enabled

### User States
1. **Anonymous**: No session or unauthenticated
2. **Pending**: Authenticated but `profile.approved == False`
3. **Approved Player**: Authenticated, active, `profile.approved == True`
4. **Staff**: Authenticated, active, `is_staff == True` or `is_superuser == True`

### Authorization Decorators
- `@api_user_required`: Requires authenticated + active user. Approval required unless `allow_pending=True`
- `@api_user_required(allow_pending=True)`: Requires authenticated + active user, but allows pending/unapproved
- `@api_staff_required`: Requires staff/superuser (implies `@api_user_required`)
- `@api_methods('GET', 'POST', ...)`: Restricts HTTP methods

### CSRF Behavior
- **Required**: All POST, PATCH, DELETE requests via session auth
- **Exempt**: Only `/profile/picture/upload/` (web route, NOT `/api/v1/`) - for iPhone/HEIF compatibility
- **API**: All `/api/v1/` mutations require CSRF token
- **Headers**: `X-CSRFToken` header or `csrfmiddlewaretoken` form field

---

## Response Format

### Success
```json
{
  "ok": true,
  "data": { ... }
}
```

### Error
```json
{
  "ok": false,
  "error": {
    "code": "error_code",
    "message": "Human readable message"
  }
}
```

Additional error fields may be included (e.g., `errors` for form validation).

### HTTP Status Codes
- `200 OK`: Success
- `400 Bad Request`: Invalid input
- `401 Unauthorized`: Authentication required
- `403 Forbidden`: Authorization denied (approved, staff, self-protection)
- `404 Not Found`: Resource not found
- `405 Method Not Allowed`: Wrong HTTP method
- `502 Bad Gateway`: External service failure (e.g., Spotify API)

---

## Approval and Access Restrictions

### Session Endpoint (`/api/v1/session/`)
- **Requires**: Authenticated + active user
- **Pending users allowed**: YES - returns `approved: false` in response
- **Anonymous**: 401 Unauthorized
- **Approved users**: returns `approved: true`

### Pending/Unapproved Users
- Can authenticate (`/api/v1/session/`)
- Can check onboarding state (`/api/v1/onboarding/`)
- CANNOT access most endpoints (403 Forbidden)
- Must be approved by staff via `/api/v1/staff/players/<pk>/action/` with `action: approve`

### Approved Players
- Can access all player endpoints
- CANNOT access staff endpoints (403)

### Staff/Superusers
- Can access all endpoints including staff endpoints
- Self-protection: Cannot deactivate or remove own staff access

---

## Endpoint Inventory

### Session / Foundation

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/` | Required | API index/health |
| GET | `/api/v1/session/` | Required (pending ok) | Current session info |

### Native Authentication

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/auth/csrf/` | Anonymous | Issue a CSRF token and cookie |
| POST | `/api/v1/auth/login/` | Anonymous | Authenticate with a username and password |
| POST | `/api/v1/auth/signup/` | Anonymous | Create an account and establish a session |
| POST | `/api/v1/auth/logout/` | Authenticated (pending ok) | End the current session |

Authentication uses Django sessions; these endpoints do not issue JWTs or API
tokens. All POST requests require the CSRF token from the `csrftoken` cookie
(the configured cookie name may differ) in the `X-CSRFToken` header.

`/api/v1/auth/login/` accepts `{ "username": "...", "password": "..." }`.
Successful login returns the same session data shape documented below.

`/api/v1/auth/signup/` accepts:

```json
{
  "display_name": "Alice",
  "username": "alice",
  "email": "alice@example.com",
  "password": "...",
  "password_confirm": "...",
  "agree_to_terms": true
}
```

Terms must be explicitly accepted with the JSON boolean `true`. Successful
signup logs the user in, but the new user's `approved` value remains `false`
until staff approval. Login and signup validation failures use the standard
error envelope with an additional `errors` object containing Django form
field errors.

Successful logout returns:

```json
{
  "ok": true,
  "data": {"authenticated": false, "user": null}
}
```

**`/api/v1/session/` Response:**
```json
{
  "ok": true,
  "data": {
    "authenticated": true,
    "user": {
      "id": 1,
      "username": "alice",
      "display_name": "Alice",
      "email": "alice@example.com",
      "approved": true,
      "is_staff": false,
      "is_superuser": false
    }
  }
}
```

---

### Player Read Endpoints

All require `@api_user_required` (authenticated + approved).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/dashboard/` | Current round + recent results |
| GET | `/api/v1/seasons/` | List of seasons |
| GET | `/api/v1/archive/` | Archived rounds |
| GET | `/api/v1/rounds/<pk>/` | Round detail |
| GET | `/api/v1/rounds/<pk>/ballot/` | Voting ballot |
| GET | `/api/v1/rounds/<pk>/results/` | Round results (after reveal) |
| GET | `/api/v1/leaderboard/` | Season leaderboard |
| GET | `/api/v1/profiles/<username>/` | User profile |
| GET | `/api/v1/profiles/<username>/achievements/` | User achievements |

---

### Submissions / Spotify

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/spotify/search/` | Required | Search Spotify tracks |
| GET | `/api/v1/rounds/<pk>/submission/` | Required | Submission status for round |
| POST | `/api/v1/rounds/<pk>/submissions/` | Required | Create submission |

**`/api/v1/spotify/search/` Query Params:**
- `q`: Search query (string, min 2 chars)
- `round`: Optional round ID for duplicate checking

**`/api/v1/spotify/search/` Response:**
```json
{
  "ok": true,
  "data": {
    "tracks": [
      {
        "id": "spotify-track-id",
        "uri": "spotify:track:...",
        "url": "https://open.spotify.com/track/...",
        "title": "Song Title",
        "artist": "Artist Name",
        "artist_ids": ["artist-id-1", "artist-id-2"],
        "album": "Album Name",
        "art": "https://i.scdn.co/image/...",
        "preview": "https://p.scdn.co/mp3-preview/..." | "",
        "explicit": false,
        "isrc": "USABC1234567" | "",
        "used": true,
        "available": false
      }
    ]
  }
}
```

**Duplicate availability fields:**
- `used`: true if ISRC matches an existing submission in the round
- `available`: true if not used (can be submitted)

**`/api/v1/rounds/<pk>/submissions/` Request Body:**
```json
{
  "track_id": "spotify-track-id"
}
```

---

### Voting

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/rounds/<pk>/votes/<submission_id>/` | Required | Save vote |

**Request Body:**
```json
{
  "score": 3
}
```

---

### Profile / Onboarding / Media / Push

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/onboarding/` | Required (pending ok) | Onboarding state |
| POST | `/api/v1/onboarding/season-welcome/` | Required | Acknowledge season welcome |
| POST | `/api/v1/onboarding/voting-guide/` | Required | Acknowledge voting guide |
| POST | `/api/v1/onboarding/submission-rules/` | Required | Accept submission rules |
| POST | `/api/v1/profile/` | Required | Update profile display name |
| POST,DELETE | `/api/v1/profile/picture/` | Required | Set/remove profile picture |
| GET | `/api/v1/notifications/` | Required | Get notification preferences |
| POST,DELETE | `/api/v1/push/subscriptions/` | Required | Manage push subscriptions |

---

### Staff Read Endpoints

All require `@api_staff_required` (authenticated + staff/superuser).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/staff/` | League overview with counts + QR code |
| GET | `/api/v1/staff/rounds/` | List all rounds with search |
| GET | `/api/v1/staff/rounds/<pk>/status/` | Per-round player participation |
| GET | `/api/v1/staff/players/` | List all players with search |
| GET | `/api/v1/staff/badges/` | List all badges with search |
| GET | `/api/v1/staff/seasons/` | List all seasons |

**`/api/v1/staff/` Response:** Includes `playlist_url` always for staff.

**Search Endpoints:**
- Players: search by username, first_name, last_name, email
- Badges: search by name, description, achievement_key
- Rounds: search by prompt, details, season name

---

### Staff Command Endpoints

All require `@api_staff_required`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/staff/rounds/create/` | Create round |
| POST,PATCH | `/api/v1/staff/rounds/<pk>/` | Edit round |
| POST | `/api/v1/staff/rounds/<pk>/action/` | Lifecycle action |
| POST | `/api/v1/staff/rounds/<pk>/archive/` | Archive round |
| DELETE | `/api/v1/staff/rounds/<pk>/delete/` | Delete round |
| POST | `/api/v1/staff/rounds/<pk>/playlist/` | Create Spotify playlist |
| POST | `/api/v1/staff/players/<pk>/action/` | User membership action |
| POST,PATCH | `/api/v1/staff/badges/create/` | Create badge |
| POST,PATCH | `/api/v1/staff/badges/<pk>/` | Edit badge |
| POST | `/api/v1/staff/badges/<badge_pk>/award/<user_pk>/` | Toggle badge award |
| POST,PATCH | `/api/v1/staff/seasons/create/` | Create season |
| POST,PATCH | `/api/v1/staff/seasons/<pk>/` | Edit season |

---

## Privacy Rules

### Ballot / Round Detail Privacy

| State | `/rounds/<pk>/` eligible_submissions | `/rounds/<pk>/ballot/` eligible_submissions |
|-------|--------------------------------------|-----------------------------------------|
| `upcoming` | NOT exposed | NOT exposed |
| `submitting` | NOT exposed | NOT exposed |
| `voting` | Exposed | Exposed |
| `locked` | NOT exposed | NOT exposed |
| `revealed` | Results via `results` field | NOT exposed |

### Playlist URL Privacy

| State | Staff API | Player API |
|-------|-----------|------------|
| Before reveal | Exposed | **Hidden** |
| After reveal | Exposed | Exposed |

---

## Business Rules Enforced by API

### Submission Rules
- ISRC-only duplicate prevention
- Explicit tracks rejected
- 4-point bonus on valid submission

### Voting Rules
- No self-voting
- Score range: 1-5
- Incremental saves
- Complete ballot required for results

### Privacy
- Pre-voting: other submissions hidden
- Voting: anonymous eligible songs exposed
- Post-voting: ballot metadata hidden, results via results endpoint
- Playlist URL: hidden from players before reveal

---

## Deployment

### Docker Compose
```bash
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput
```

### Migration Note
Migration `0014_submission_isrc_unique.py` requires `python manage.py migrate` when deploying this branch.

---

## Data Safety
- Non-destructive changes
- Historical integrity preserved
- Soft deletes (archive) preferred

---

## Version
This contract corresponds to QueueUp `migration/frontend-backend-split` branch with privacy regression fixes and comprehensive test coverage.
