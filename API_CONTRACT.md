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
- `@api_staff_required`: Requires staff/superuser (implies `@api_user_required`)
- `@api_methods('GET', 'POST', ...)`: Restricts HTTP methods

### CSRF Behavior
- **Required**: All POST, PATCH, DELETE requests via session auth
- **Exempt**: Only `/profile/picture/upload/` (web route, not API) - for iPhone/HEIF compatibility
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
- `201 Created`: Resource created (not consistently used - may return 200)
- `400 Bad Request`: Invalid input
- `401 Unauthorized`: Authentication required
- `403 Forbidden`: Authorization denied (approved, staff, self-protection)
- `404 Not Found": Resource not found
- `405 Method Not Allowed`: Wrong HTTP method
- `502 Bad Gateway": External service failure (e.g., Spotify API)

---

## Approval and Access Restrictions

### Pending/Unapproved Users
- Can authenticate (`/api/v1/session/`)
- Can check onboarding state (`/api/v1/onboarding/`)
- CANNOT access most endpoints (403 Forbidden)
- Must be approved by staff via `/api/v1/staff/players/<pk>/action/` with `action: approve`

### Approved Players
- Can access all player endpoints
- CANNOT access staff endpoints (403)
- Can view rounds, submit, vote, view results (when revealed)

### Staff/Superusers
- Can access all endpoints including staff endpoints
- Self-protection: Cannot deactivate or remove own staff access

---

## Endpoint Inventory

### Session / Foundation

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/` | Required | API index/health |
| GET | `/api/v1/session/` | Optional | Current session info |

**`/api/v1/session/` Response:**
```json
{
  "ok": true,
  "data": {
    "user": {
      "id": 1,
      "username": "alice",
      "is_approved": true,
      "is_staff": false
    } | null
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
- `q`: Search query
- `round`: Optional round ID for duplicate checking

**Response:**
```json
{
  "ok": true,
  "data": {
    "tracks": [
      {
        "id": "spotify-track-id",
        "name": "Song Title",
        "artist": "Artist Name",
        "album": "Album Name",
        "uri": "spotify:track:...",
        "isrc": "USABC1234567",
        "explicit": false,
        "already_taken": true | false
      }
    ]
  }
}
```

**`/api/v1/rounds/<pk>/submissions/` Request Body:**
```json
{
  "track_id": "spotify-track-id"
}
```

**Submission Validation:**
- ISRC-only duplicate prevention within same round
- Explicit tracks rejected
- Submission rules must be accepted
- Only one submission per user per round
- Server-side Spotify verification

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

**Rules:**
- Score must be 1-5
- Cannot vote for own submission (403)
- Voting must be open
- Votes are saved incrementally
- Editing one vote preserves others
- Saved ratings preload on return

**Voting Complete:**
- User must complete all eligible ratings
- After deadline: incomplete ballots contribute ZERO ratings to results
- Before deadline: partial votes remain saved and editable

---

### Profile / Onboarding / Media / Push

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/onboarding/` | Required (pending ok) | Onboarding state |
| POST | `/api/v1/onboarding/season-welcome/` | Required | Acknowledge season welcome |
| POST | `/api/v1/onboarding/voting-guide/` | Required | Acknowledge voting guide |
| POST | `/api/v1/onboarding/submission-rules/` | Required | Accept submission rules |
| PATCH | `/api/v1/profile/` | Required | Update profile |
| GET,POST | `/api/v1/profile/picture/` | Required | Get/set profile picture |
| GET,PATCH | `/api/v1/notifications/` | Required | Notification preferences |
| GET,POST | `/api/v1/push/subscriptions/` | Required | Push subscription management |

**Profile Picture Upload:**
- Endpoint: `/api/v1/profile/picture/`
- Multipart form data with `picture` field
- Supported: JPEG, PNG, GIF, WebP, HEIF/HEIC
- Max size: 5 MB
- HEIF/HEIC converted to JPEG server-side

**CSRF Note:** The web-only `/profile/picture/upload/` endpoint (NOT `/api/v1/profile/picture/`) is CSRF-exempt for iPhone compatibility. The API endpoint requires CSRF.

**Season Welcome:**
- New users see current season welcome prompt
- Acknowledgment persisted via `SeasonWelcome` model
- Idempotent: multiple calls = single record

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

**`/api/v1/staff/` Response:**
```json
{
  "ok": true,
  "data": {
    "round_count": 5,
    "badge_count": 10,
    "season_count": 2,
    "user_count": 15,
    "signup_url": "http://.../signup/",
    "signup_qr": "base64-encoded-png",
    "spotify_connection": {
      "display_name": "Staff User",
      "spotify_user_id": "spotify-id"
    } | null
  }
}
```

**`/api/v1/staff/rounds/` Query Params:**
- `q`: Search query (prompt, details, season name)

**`/api/v1/staff/rounds/` Response:**
```json
{
  "ok": true,
  "data": {
    "query": "search term",
    "rounds": [
      {
        "id": 1,
        "season_id": 1,
        "season": "Season 1",
        "prompt": "Round 1",
        "details": "...",
        "state": "submitting",
        "is_draft": false,
        "archived": false,
        "submission_count": 5,
        "vote_count": 10,
        "submitted_player_count": 5,
        "completed_voter_count": 2,
        "league_player_count": 8,
        "playlist_url": "https://..." | null
      }
    ]
  }
}
```

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

**Round Lifecycle Actions:**
- `open_submissions`: Open for submissions
- `open_voting`: Open for voting
- `lock_voting`: Lock voting
- `reveal`: Reveal results

**User Membership Actions:**
- `approve`: Approve user for league access
- `toggle_active`: Activate/deactivate user
- `make_staff`: Grant staff access
- `remove_staff`: Remove staff access

**Self-Protection:**
- Staff cannot deactivate themselves
- Staff cannot remove their own staff access
- Returns 403 with code `self_access_change_not_allowed`

**Archive Rules:**
- Only revealed rounds can be archived
- Archived rounds: removed from homepage, kept in Archive page
- Returns 403 with code `round_not_revealed` if not revealed

**Playlist Creation:**
- Requires staff Spotify connection
- Creates private playlist with round submissions
- Saves playlist URL to round

**Badge Award:**
- Toggle: POST same endpoint again to remove award
- Returns `{awarded: true/false}`

---

## Business Rules Enforced by API

### Submission Rules
1. **ISRC-Only Duplicates**: Duplicate prevention uses ISRC only, not title/artist
2. **Per-Round**: Only checks within the same round
3. **Historical Compatibility**: NULL ISRC submissions predate ISRC storage; not retroactively altered
4. **4-Point Bonus**: Awarded immediately on valid submission
5. **Deletion/Replacement**: Deleting submission removes 4-point bonus; replacement restores it

### Voting Rules
1. **No Self-Voting**: Users cannot vote for their own submission (403)
2. **Score Range**: 1-5 only
3. **Incremental**: Votes saved individually, editing one preserves others
4. **Preloading**: Saved ratings must preload when user returns
5. **Complete Ballot**: After deadline, incomplete ballots contribute ZERO to results
6. **Before Deadline**: Partial votes remain saved and editable

### Results/Scoring Rules
1. **Average Rating**: Song results based on average eligible rating
2. **Submission Bonus**: 4 points awarded immediately for each valid submission
3. **Complete Ballot Filtering**: Incomplete ballots excluded from all calculations
4. **Consistency**: Leaderboard, profile stats, wins, podiums, achievements use same eligibility

### Achievements
1. **Genre Hopper**: 10 distinct main genres from revealed rounds only
2. **Genre Normalization**: One main genre per song (e.g., "Christian hip hop" -> "Hip hop")
3. **No Retroactive Counting**: Historical submissions not altered

### Anonymity
1. **Pre-Reveal**: Submission content (title, artist) hidden from other players
2. **Submission Points Visible**: It IS acceptable for users to see that someone received 4-point submission bonus
3. **Post-Reveal**: All information visible

### Round Lifecycle
1. **States**: upcoming, submitting, voting, locked, revealed, archived
2. **Homepage Ordering**: New submitting round first, then previous results; archived excluded
3. **Archive**: Only revealed rounds can be archived
4. **Transitions**: Manual actions validated (cannot open voting before submissions, etc.)

### Media
1. **Profile Picture**: Separate form from normal profile editing
2. **CSRF Exemption**: ONLY `/profile/picture/upload/` (web route) is CSRF-exempt
3. **API Picture Endpoint**: `/api/v1/profile/picture/` requires CSRF
4. **Formats**: JPEG, PNG, GIF, WebP, HEIF/HEIC
5. **Conversion**: HEIF/HEIC converted to JPEG server-side

### Realtime Authorization
1. **Anonymous**: Rejected
2. **Pending/Unapproved**: Rejected
3. **Approved Active**: Allowed
4. **Staff/Superuser**: Allowed
5. **Approval Required**: Must be approved for realtime connections

### Push Notifications
1. **Approval Push**: Sent when user approved (idempotent)
2. **Event Keys**: e.g., `user:<pk>:approved`, `round_updated`
3. **Subscription Management**: `/api/v1/push/subscriptions/`
4. **Multiple Devices**: Supported

---

## Deployment Architecture

### Current State: Monolith

QueueUp currently runs as a single Django application (monolith) with:
- Django web server
- PostgreSQL database
- Redis for channels/realtime
- Static files via whitenoise
- Docker Compose deployment

### Future Split: queueup-backend + queueup-webapp + queueup-mobile

#### queueup-backend (Future)
Owns:
- Django application
- Database models and migrations
- Authentication and approval workflow
- Season, round, submission, voting business logic
- Scoring, leaderboard, achievements
- Spotify server integration (token exchange, API calls)
- Notification and push services
- Media storage and processing
- Realtime authorization (WebSocket/Django Channels)
- ALL `/api/v1/` endpoints
- HTML server-side rendering (temporary, for migration compatibility)

#### queueup-webapp (Future)
Owns:
- Browser-based PWA frontend
- Consumes `/api/v1/` via fetch/XHR
- Does NOT duplicate business rules
- UI/UX presentation only
- Client-side routing
- State management

#### queueup-mobile (Future)
Owns:
- Mobile client (iOS/Android/React Native/etc.)
- Consumes the same `/api/v1/` as webapp
- Does NOT duplicate business rules
- Mobile-specific UI

### Migration Strategy

1. **Phase 1 (Current)**: Complete API implementation (this branch)
2. **Phase 2**: Build queueup-webapp consuming `/api/v1/`
3. **Phase 3**: Migrate existing HTML templates to webapp
4. **Phase 4**: Deploy webapp alongside backend
5. **Phase 5**: Optional: Build queueup-mobile
6. **Phase N**: Optional: Split backend into separate service

**Do NOT split repositories yet.** All code remains in this monorepo until the API contract is proven stable.

---

## Production Deployment

### Docker Compose (Current)

```bash
# Initial setup
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput

# After code changes (no migration)
docker compose up --build -d
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput

# After code changes (with migration)
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput
```

### Environment Configuration

Required settings (see `.env.example`):
- `DJANGO_SECRET_KEY`
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `POSTGRES_HOST`, `POSTGRES_PORT`
- `REDIS_URL`
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`
- `SPOTIFY_REDIRECT_URI`
- `PUBLIC_URL` (for signup QR codes)
- `WEBPUSH_*` (optional, for push notifications)
- `EMAIL_*` (optional, for password reset)

### No Migration Required

The Staff API feature (`feat: add staff api`) adds NO database migrations. It uses existing models with service-layer queries and annotations.

---

## Data Safety

- **Non-destructive**: Staff API reads use existing data
- **Deletion**: Round/delete and user actions preserve business rules
- **Historical Integrity**: No retroactive modifications to completed rounds
- **Soft Deletes**: Archive rounds (not delete), approve/revoke users
- **Cascading**: Be aware that deleting submissions after voting starts may cascade-delete votes

---

## Known Invariants

1. **No Submissions × Votes Multiplication**: Count queries use `distinct=True` or separate subqueries
2. **ISRC Only Duplicates**: No title/artist matching
3. **Staff Search**: Players by username/first/last/email; Badges by name/description/achievement_key; Rounds by prompt/details/season
4. **Round Status Aggregates**: Authoritative counts via `round_status_service`
5. **Complete Ballot Filtering**: Incomplete ballots excluded from ALL scoring calculations
6. **Submission Bonus**: 4 points immediate, derived from submissions (not stored balance)

---

## Version

This API contract corresponds to QueueUp migration `migration/frontend-backend-split` branch.
Current committed state includes Staff API implementation with 8 new test cases.
