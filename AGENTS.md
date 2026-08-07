# QueueUp Project Context

## What QueueUp is

QueueUp is a self-hosted Django Progressive Web App for a small private music league.

Core loop:
1. Admin creates a round/prompt.
2. Users submit one song.
3. Submissions lock.
4. Voting opens.
5. Users rate eligible songs.
6. Results reveal.
7. Points, leaderboard stats, prestige, and achievements update.

The app is designed for a small trusted friend group, not public-scale multi-tenant use.

---

## Deployment

QueueUp runs with Docker Compose.

Typical deployment:

```bash
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput
```

There is no nginx layer in front of Django.

Cloudflare is used externally.

---

## Current version

Current working release: v7.2.2

Recent release history:

- v5.5
- v5.5.1
- v5.5.2
- v5.5.3
- v5.5.4
- v5.5.5
- v5.5.5 migration fix
- v6.0
- v6.0 visual QA
- v7.0
- v7.0 migration fix
- v7.0.1 CSRF reset
- v7.0.2 template fix
- v7.0.3 separate profile-picture upload
- v7.0.4 iPhone picture-upload fallback
- v7.0.5 JPEG compatibility
- v7.0.6 HEIF conversion
- v7.0.7 round stats
- v7.0.8 complete ballots only
- v7.0.9 homepage previous-results + new-round display
- v7.0.10 archive rounds + homepage ordering
- v7.0.11 Genre Hopper fix
- v7.1 vote review + Spotify preview fixes
- v7.1.1 Spotify pause fix
- v7.1.2 ISRC duplicate prevention
- v7.2 admin refresh + submission points
- v7.2.1 immediate 4-point submission bonus
- v7.2.2 round participation counters

---

# Important Product Rules

## Submission bonus

Submitting a song awards **4 points immediately**.

This applies as soon as the submission is accepted, not after reveal.

It is acceptable for users to see that someone received submission points. The song itself must remain anonymous until reveal.

If a submission is deleted/reset, the associated 4-point contribution should disappear automatically. When that user submits a replacement, the 4 points should return.

Avoid storing the 4-point reward as a manually incremented balance unless necessary. Prefer deriving it from existing submissions so deleting/replacing submissions stays consistent.

---

## Duplicate songs

Duplicate prevention is **ISRC-only**.

For future submissions:
- Save the Spotify ISRC on the Submission.
- If another submission in the same round has the same ISRC, block it.
- In Spotify search results, duplicate-ISRC tracks should be visibly disabled/grayed out as “Already taken.”
- Server-side validation must repeat the same check.

Do NOT use title/artist matching as a duplicate rule.

Do NOT retroactively alter historical submissions that predate ISRC storage.

---

## Voting

Users cannot vote for their own submission.

Votes can be saved incrementally.

When a user returns to review/edit votes:
- Previously saved ratings must preload.
- Existing stars must visibly show the saved score.
- Editing one song must update only that vote.
- Other ratings must remain unchanged.

### Incomplete ballot rule

After voting closes, a voter’s ratings only count if they completed every rating they were eligible to cast.

If a voter started but did not finish:
- Keep their Vote records in the database for audit/status display.
- Exclude **all** of that voter’s ratings from results.
- Do not count partial ballots in averages, rankings, leaderboard points, wins, podiums, or score-based achievements.

Before the voting deadline, partial votes remain saved and editable.

---

## Round stats

Staff/admins have a per-round status page.

It should show:
- each league member
- their submitted song, if any
- whether they submitted
- voting progress
- voting state:
  - Not started
  - In progress
  - Complete
- exact rating progress such as `3 of 5 eligible songs rated`

Only staff/admins may access this page.

The round admin card also shows:
- total songs
- total ratings
- X/Y league members submitted
- X/Y league members completed voting

“Voted” means completed every eligible rating, not merely started.

Be careful when annotating both submission and vote counts in the same queryset: Django joins can multiply rows.

Use `Count(..., distinct=True)` or separate subqueries as appropriate.

There was a prior bug where counts looked like `2067 songs · 2067 ratings` because submissions and votes were multiplied by a join.

---

# Round Lifecycle

Typical round phases:

1. Upcoming / visible
2. Submitting
3. Voting
4. Locked / awaiting reveal
5. Revealed
6. Archived

Admins can manually:
- open submissions
- open voting
- lock
- reveal
- archive completed rounds

Manual phase changes should not destroy submissions or votes.

There has been a bug involving manually opened voting rounds and editing `voting_deadline`. Be cautious with form validation around:
- submission deadline
- voting deadline
- reveal time
- hidden seconds in datetime values

The automatic 2-day voting duration is a default, not a required minimum.

---

# Homepage Round Ordering

The homepage can show both:
- the most recent completed/revealed round
- the next/new round

Ordering rules:

- If the new round is only visible/upcoming and submissions have not opened yet:
  - previous results appear first
  - upcoming round appears below

- Once the new round enters the submission phase:
  - new round appears first
  - previous results appear below

- Archived rounds must not appear on the homepage.

Archived rounds remain available through the Archive page.

---

# Archive Behavior

Admins can archive a completed round.

Archiving:
- removes the round from the homepage
- does not delete anything
- keeps submissions, votes, scores, and results
- keeps the round accessible in the Archive
- should only be permitted for completed/revealed rounds

---

# Achievements

## Genre Hopper

Genre Hopper must use **one main genre per song**.

Do not count every Spotify subgenre tag.

Examples of normalization:
- Christian hip hop -> Hip hop
- Gospel rap -> Hip hop
- Alternative rock -> Rock
- Dance pop -> Pop

Only submissions from completed/revealed rounds count toward Genre Hopper.

Requirement remains 10 distinct main genres.

Historical incorrect Genre Hopper unlocks were cleared once when this was fixed.

Be careful about reintroducing raw Spotify genre-tag counting.

---

# Profile Picture Upload

This area has special handling due to Safari/iPhone bugs.

Normal profile editing and picture upload are separate forms.

## Normal profile form

Endpoint: `/profile/edit/`

Still CSRF-protected.

Used for:
- display name
- profile-picture removal

Do NOT exempt this view from CSRF.

## Picture upload

Endpoint: `/profile/picture/upload/`

This endpoint is intentionally:
- login required
- POST only
- CSRF exempt

Do not make CSRF exemptions broader than this endpoint.

Why: Some iPhones repeatedly failed multipart profile uploads with `403 Forbidden - CSRF token missing` even though the form contained a valid CSRF token, login/logout worked, other forms worked, and JavaScript/service-worker changes did not help.

The profile upload path now includes iPhone compatibility handling.

Supported image behavior should include:
- JPEG/JPG
- PNG
- GIF
- WebP
- HEIF/HEIC when possible
- Apple files that appear as `.jpeg` but contain HEIF data

HEIF images are decoded server-side and converted to a real JPEG.

Do not casually remove `pillow-heif` or the iPhone fallback logic.

---

# Spotify Player Behavior

Voting cards contain Spotify embeds/previews.

Expected behavior:
- only one preview should play at a time
- moving to another card should immediately stop the previous preview

Preferred implementation:
- use Spotify’s iframe controller API to pause the player
- keep the embed loaded if pausing works

Fallback:
- unload/recreate the iframe if a browser/player cannot be paused reliably

Navigation paths that must stop playback:
- Next
- Previous
- swipe
- keyboard arrows
- automatic advancement after saving a vote

---

# Admin / League Control

The admin-facing League Control UI is split into separate sections rather than one huge page.

Main controls link to dedicated searchable pages for:
- Rounds
- Badges
- Players

Each page should retain its management actions and have a search field.

Search expectations:

Rounds:
- prompt
- details
- season

Badges:
- badge name
- description
- achievement key

Players:
- display name
- username
- email

Do not collapse these back into one giant page unless explicitly requested.

---

# User Approval

QueueUp has an approval workflow.

New users require approval before normal use.

There is a waiting-for-approval screen.

Approval behavior should not be casually bypassed.

---

# Existing v7 Features

The app already includes:
- user approval
- waiting-for-approval page
- explicit music flag
- explicit music blocking
- submission-rules popup
- first-time voting guide
- round live dates
- reminder notifications
- Terms page
- Privacy page
- mobile logout
- hidden submission history

Avoid regressions to these features when modifying unrelated areas.

---

# Template History

There was a prior malformed-template bug in `stats.html`.

A `<form>` accidentally appeared inside `{% block title %}`, which produced broken HTML inside `<title>`.

This has already been fixed.

Do not reintroduce forms, markup, or large blocks inside the title block.

---

# Migration History / Cautions

There was an earlier migration problem because an `approved` database column already existed.

That was resolved using `SeparateDatabaseAndState`.

Do not rewrite or “clean up” old migrations casually.

New schema changes should receive normal forward migrations.

Existing historical migrations should generally remain immutable.

---

# Data Safety

This app contains live league history.

Prefer additive/non-destructive changes.

Unless explicitly requested:
- do not delete historical votes
- do not delete historical submissions
- do not rewrite completed-round results
- do not retroactively modify old rounds
- do not rewrite migration history

When resetting one user’s current submission, deleting that Submission is acceptable during the submission stage. They may then submit a replacement.

Be aware that deleting a submission after voting starts may cascade-delete votes attached to that submission.

---

# Scoring

Song results are based on average eligible rating rather than simple total votes.

Incomplete ballots must be excluded after the voting deadline.

Leaderboard scoring also includes the immediate **4-point submission bonus**.

When changing scoring code, check every place that independently calculates:
- round results
- leaderboard
- profile stats
- wins
- podiums
- achievements

Avoid having different score eligibility rules in different views.

A shared helper/service function is preferable.

---

# Development Guidance for Agents

Before making a change:

1. Read the relevant models, views, templates, and existing tests.
2. Find whether the same logic is duplicated elsewhere.
3. Preserve existing behavior unless the requested change specifically alters it.
4. Add regression tests for every bug fix.
5. Avoid unrelated refactors in feature releases.
6. Check whether a migration is actually required.
7. If adding a migration, never alter existing historical migrations unless fixing a known migration-specific issue.
8. Run:
   - Python syntax checks
   - Django `manage.py check`
   - Django tests
   - JavaScript syntax checks where relevant
9. Keep Docker deployment compatibility.

---

# Preferred Release Style

When making a new QueueUp release:
- bump version consistently
- include the complete project, not a patch-only archive
- keep release scope narrow
- state whether migration is required
- preserve Docker deployment behavior

Recommended deployment instructions without migration:

```bash
docker compose up --build -d
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput
```

With migration:

```bash
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput
```

---

# Product Philosophy

QueueUp is intentionally simple.

Priorities:
1. reliability
2. fairness
3. mobile usability
4. clear admin controls
5. preserving league history

Do not over-engineer solutions for large-scale/public use unless explicitly requested.

When multiple technical solutions exist, prefer the smallest robust solution that works well for a trusted private friend group.
