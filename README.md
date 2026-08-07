# QueueUp

QueueUp is a self-hosted Django Progressive Web App (PWA) for a private music league.

Players submit one song each round, vote anonymously, and track results, leaderboard movement, and achievements over time.

## What QueueUp includes

- Round lifecycle management (upcoming, submitting, voting, locked, revealed, archived)
- Spotify-powered song search and track previews
- Anonymous voting with saved-progress review/edit support
- Fair scoring rules, including complete-ballot-only counting after deadlines
- Immediate submission bonus points (4 points when a valid submission is accepted)
- League leaderboard, profile stats, achievements, and prestige badges
- Staff/admin league control pages for rounds, players, and badges
- User approval flow for new accounts
- Optional push notifications for round events and reminders
- Mobile-friendly installable PWA experience

## How the music league works

1. Admin creates a round prompt.
2. Members submit one song.
3. Submissions lock at the submission deadline.
4. Voting opens.
5. Members rate every song they are eligible to rate (not their own).
6. Voting closes and results reveal.
7. Leaderboard/stat updates are applied.

## Docker installation and deployment

### 1) Configure environment variables

Copy the example file and edit values for your environment:

```bash
cp .env.example .env
```

Important values include:

- `DJANGO_SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- PostgreSQL settings (`POSTGRES_*`)
- Spotify settings (`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`)
- `PUBLIC_URL`
- Optional web push settings (`WEBPUSH_*`)
- Optional `HOST_PORT` override (default is `8080`)

### 2) Start the stack

QueueUp uses Docker Compose with three services:

- `web` (Django + Daphne)
- `db` (PostgreSQL)
- `redis` (channel/message backend)

Start everything:

```bash
docker compose up --build -d
```

### 3) Run setup checks

Typical deployment/upgrade commands:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput
```

QueueUp is served directly by Django/Daphne in this setup (no nginx layer required).

## Basic admin workflow

After first startup:

1. Create/login with a staff or superuser account.
2. Approve new members from League Control as needed.
3. Create a season (if needed) and create a round prompt.
4. Open submissions.
5. Open voting after submissions close.
6. Lock and reveal results.
7. Optionally archive completed rounds.

## Upgrading QueueUp

1. Back up your database and media volume.
2. Pull/replace application files with the new release.
3. Keep your existing `.env`, PostgreSQL volume, and `media_data` volume.
4. Rebuild and restart:

```bash
docker compose up --build -d
```

5. Run:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput
```

## Additional documentation

- Release history: [CHANGELOG.md](CHANGELOG.md)
- Maintainer/project guidance: [AGENTS.md](AGENTS.md)
