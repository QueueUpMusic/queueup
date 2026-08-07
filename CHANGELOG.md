# QueueUp Changelog

## Version order (latest first)

- v7.2.2
- v7.2.1
- v7.1.2
- v7.1.1
- v7.0.11
- v7.0.10
- v7.0.9
- v7.0.8
- v7.0.7
- v7.0.6
- v7.0.5
- v7.0.4
- v7.0.1
- v7.0
- v6.0
- v5.5.5
- v5.5.2
- v5.5.1
- v5.5
- v5.4.1
- v5.4
- v5.2
- v5.1
- v5
- v4

## Historical release notes (preserved)

# QueueUp v7.0.11

Genre Hopper now counts one broad main genre per song and only revealed rounds. Existing Genre Hopper unlock records are reset by migration 0011 so they can be recalculated correctly.

# QueueUp v7.0.6 - iPhone HEIF conversion fix

This release fixes iPhones that show a selected photo as `IMG_xxxx.jpeg` while the underlying file data is still HEIC/HEIF.

- Adds `pillow-heif` support in the Docker build.
- Detects the actual image format from its bytes rather than trusting the filename or MIME type.
- Converts HEIC/HEIF uploads to a real JPEG before saving the profile picture.
- Handles both the raw iPhone upload path and normal multipart uploads.
- Keeps the 5 MB limit and existing JPG/JPEG, PNG, GIF, and WebP support.
- No migrations or settings changes.

# QueueUp v7.0

QueueUp v7.0 adds clean-music enforcement, first-time voting and submission guidance, six-hour targeted reminders, scheduled round visibility, staff approval for new accounts, privacy protections for submission history, and a mobile profile logout.

## Upgrade from v6.0

Keep your existing `.env`, PostgreSQL volume, and `media_data` volume. Existing users are automatically approved by migration `0009`; only accounts created after the upgrade enter the approval queue.

```bash
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py test
docker compose exec web python manage.py collectstatic --noinput
```

Keep `send_round_notifications` scheduled at least every five minutes. Six-hour reminders are idempotent and are sent only to approved users who still need to submit or finish voting.


## v7.0.5 JPEG compatibility fix

- Treats `.jpg` and `.jpeg` as the same JPEG image format.
- Normalizes iPhone `.jpeg` uploads to a standard `.jpg` filename and `image/jpeg` MIME type.
- Detects the real image format from the uploaded bytes when iOS reports an empty or generic MIME type.
- No migration or environment-variable change is required.


## v7.0.4 iPhone profile-picture upload fix

- Profile-picture uploads use a profile-page-only raw-image fallback so affected iPhones do not lose the file in a malformed or empty multipart request.
- The normal multipart upload remains available as a no-JavaScript fallback and for unaffected browsers.
- Unsupported iPhone photo formats are converted in the browser to JPEG before upload when possible.
- Display-name and picture controls are now separated with clearer spacing.
- Removing the current picture now lives in the profile-picture card and remains CSRF-protected.
- No migration or environment-variable change is required.

## v7.0 highlights

- Spotify tracks marked explicit are visibly blocked in search and rejected again by the server.
- First-time voting and clean-submission guides are remembered per user.
- New rounds can remain completely hidden until `goes_live_at`.
- Current active rounds take priority on Home; upcoming rounds appear only when no active round exists.
- New accounts wait for staff approval while retaining access to notification settings.
- Approval sends a push notification to subscribed devices.
- Profile submission history includes only revealed rounds.
- Terms of Use and Privacy Policy starter pages are included.
- PWA cache and asset version updated to `queueup-v7.0`.

## QueueUp v6.0 responsive review

- Reviewed representative public, player, round, voting, results, profile, leaderboard, archive, notification, and staff-control screens at 320, 390, 430, 768, 1024, and 1440 pixel widths.
- Added consistent primary, secondary, form, empty-state, search, modal, profile, result, and control-panel styling.
- Corrected narrow-screen overflow from long player names and usernames in League Control.
- Improved mobile button grids, result cards, profile badges, voting controls, landing/auth pages, season welcome presentation, and safe-area spacing.
- Fixed the transparent song-lock and season-welcome modal surface caused by an undefined CSS variable.
- Removed the obsolete browser confirmation so the song-lock flow uses only the in-app confirmation.
- Corrected invalid duplicate song-confirmation markup in the song-picker template.
- Bumped the PWA service-worker cache to `queueup-v6.0` and versioned the CSS, JavaScript, and manifest references so devices fetch the refreshed interface.
- No database migration or new environment variable is required for v6.0.

### Upgrade from v5.5.5

Keep the existing `.env`, PostgreSQL volume, and media volume. Replace the application files, then run:

```bash
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput
```

## v5 interface overhaul

- Renamed throughout from Music League to **QueueUp**
- New local open-source-style SVG icon system; no external CDN required
- Unified spacing, cards, forms, buttons, navigation, lists, and empty states
- Responsive admin create/edit forms and control-panel actions
- Rebuilt leaderboard, archive, authentication, profiles, voting, and search layouts
- Updated PWA name, icon, theme colors, notifications, and generated playlist descriptions

# QueueUp v4

A polished, mobile-first, self-hosted music competition for friends.

## v4 features

- Instant Spotify search and pasted-track links
- Anonymous swipeable voting with touch and keyboard controls
- Official Spotify embeds and Open in Spotify fallback
- Animated winner reveal with confetti and podium results
- Profiles with wins, podiums, average placement, win rate, favorite artists/genres, history, and badges
- Seasonal leaderboards: rankings persist across rounds and reset when a new season is selected
- WebSocket live updates for submissions, votes, and staff phase changes
- Browser push notifications
- Installable PWA and polished Spotify-inspired responsive dark UI
- Custom League Control dashboard: create/edit/delete rounds, phase controls, player access management, signup QR
- Optional admin Spotify connection for one-click private playlist creation
- Django admin remains available as a maintenance fallback

## Upgrade from v3

Keep your existing `.env` and database volume. Replace the application files, then add these values to `.env`:

```env
PUBLIC_URL=http://YOUR-SERVER-IP:8080
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8080/control/spotify/callback/
```

For a public HTTPS deployment, both should use your real HTTPS domain. Then run:

```bash
docker compose down
docker compose up --build -d
docker compose logs -f web
```

The new migration runs automatically. Redis is included for WebSockets.

## Spotify setup

Catalog search uses Client Credentials and does not require users to connect Spotify. For automatic playlists, add the exact redirect URI from `SPOTIFY_REDIRECT_URI` to your Spotify app dashboard, then use **League Control → Connect Spotify**. Only the staff account creating playlists authorizes Spotify.

Spotify currently requires HTTPS redirect URIs except for explicit loopback IP addresses such as `127.0.0.1`; `localhost` is not accepted.

## Push notifications

Generate VAPID keys:

```bash
docker compose run --rm web python manage.py generate_vapid_keys
```

Add them to `.env`, restart, and schedule:

```cron
*/5 * * * * cd /home/micah/music-league && docker compose exec -T web python manage.py send_round_notifications
```

Push requires HTTPS on phones and normal remote browsers.


## Profile pictures (v5.1)
Players can upload a profile picture from **Profile → Edit profile**. Images are stored in the persistent `media_data` Docker volume, so they survive container rebuilds. Existing users receive a profile automatically during migration.

## QueueUp v5.2

- User-uploaded profile pictures and display names throughout the app
- Countdown timers always include days, hours, minutes, and seconds
- Mobile-friendly five-star voting control with larger touch targets
- Confetti now falls naturally, clears itself, and disappears after the reveal animation

After upgrading, run migrations because profile pictures add the `UserProfile` model:

```bash
docker compose up --build -d
docker compose exec web python manage.py migrate
```


## v5.4 updates
- Rebuilt the 1–5 star control as five independent accessible buttons with one icon each.
- Added notifications for newly announced rounds, submissions opening, 24-hour submission reminders, voting opening, 24-hour voting reminders, results, and newly earned achievements.
- Run `python manage.py migrate` after upgrading.
- Keep the existing cron entry for `send_round_notifications`; checking every minute is supported.

## v5.4.1 mobile/PWA fixes
- Added manifest scope `/` so Ranks, Profile, Archive, Control, and round pages stay inside the installed PWA.
- Added iPhone safe-area handling so the QueueUp header sits below the status bar.
- Push subscriptions remain active across every future round until the user explicitly disables them or the browser revokes the subscription.
- Deadline reminders are now targeted to players who still need to submit or finish voting.
- If QueueUp was already installed on iPhone before this update, remove the old Home Screen app and add it again once so iOS adopts the corrected manifest scope.

## QueueUp v5.5

### What changed
- Notifications are one account-wide feature with one subscription per user/browser device. The settings page verifies the current browser's real permission, service worker, and PushManager subscription state.
- iPhone and iPad guidance explains that QueueUp must be installed and opened as a Home Screen app before web push can be enabled.
- Notification delivery is idempotent per event and device through `NotificationDelivery`, so `send_round_notifications` remains safe to run every minute.
- The final saved vote redirects to a durable voting-complete screen. Reloading or revisiting still shows the completed state, while review mode permits edits.
- Saved/saving/error feedback now appears in its own live region below the complete star row.
- Results, winner badges, profile statistics, and season standings use standard competition ranking: equal scores share a place and the next place skips appropriately (1, 1, 3).
- The v5.5 PWA service-worker cache was refreshed; v6.0 now uses `queueup-v6.0`; navigation HTML is fetched from the network and authenticated pages are not cached.

### Upgrade from v5.4.1

Keep your existing `.env`, PostgreSQL volume, and `media_data` volume. Replace the application files with this release, then run:

```bash
docker compose config
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py test
```

Keep the existing cron command; running it every minute is supported:

```cron
* * * * * cd /path/to/queueup && docker compose exec -T web python manage.py send_round_notifications >/dev/null 2>&1
```

No new environment variables are required. Existing `WEBPUSH_PUBLIC_KEY`, `WEBPUSH_PRIVATE_KEY`, and `WEBPUSH_CONTACT` values continue to be used.

### Migration added
- `league/migrations/0007_notificationdelivery.py`

The migration adds only notification delivery receipts. It does not remove or rewrite existing users, rounds, votes, submissions, achievements, profile pictures, seasons, or push subscriptions.

## v5.5.1 interface fixes

- Added a Notifications shortcut to the signed-in user's profile page.
- Replaced transparent PWA icons with opaque green-background icons so iOS dark-mode icon styling cannot produce a black-on-black Home Screen icon.
- Added a dedicated 180x180 Apple touch icon and refreshed the manifest version reference.
- Shortened the notification settings copy and push-notification titles for cleaner mobile display.

After deploying, remove the existing QueueUp Home Screen icon and add it again so iOS downloads the new icon instead of using its cached copy.


## v5.5.2 mobile profile fix

- Corrected the profile hero grid on phone and tablet widths so the avatar, username, join date, and profile actions no longer squeeze or overflow.
- Reduced the mobile profile heading and avatar sizes at narrow widths.
- Bumped the PWA cache to `queueup-v5.5.2` so the corrected stylesheet is fetched after deployment.

## QueueUp v5.5.5

- Added long-term and hidden achievements, including multi-season and high-win milestones.
- Added prestige badges beside player names; admins can create, edit, and manually award badges in League Control.
- Added season descriptions/banner uploads and a one-time new-season welcome for each player.
- Replaced the browser song-lock confirmation with an in-app confirmation that clearly explains the choice is final.
- Added consistent spacing between League Control sections.


## v7.0.1 CSRF compatibility fix

- Rotated QueueUp to a dedicated CSRF cookie name to avoid stale or duplicate Safari/PWA cookies.
- Removed the profile-upload JavaScript token manipulation.
- Login and profile-edit pages are served with fresh CSRF cookies and no-cache headers.
- Updated the service-worker cache version to queueup-v7.0.1.


## v7.0.7

- Added a staff-only status page for each round.
- Shows every approved/staff player, their submitted song, and whether voting is not started, in progress, or complete.
- Linked from the existing controls for each round.
- No database migrations.

## v7.0.8

- After a round's voting deadline, a voter's ratings count only when that voter completed every eligible rating in the round.
- An incomplete voter's entire ballot is excluded from round rankings, profile averages, leaderboard totals, and achievement calculations.
- Partial votes remain stored so staff can still see that voting was started but not completed.
- No database migration is required.

## v7.0.9

- Keeps the most recently revealed round's results on the homepage when the next round becomes visible.
- Displays the results card above the new/upcoming round card.
- No database migration required.


## v7.0.10

- Added a staff Archive button for completed rounds. Archived rounds leave the homepage but remain available in the Archive.
- Upcoming visible rounds stay below the latest results until submissions open.
- Once submissions open, the new round moves above the previous results and remains first through voting and reveal lock.
- Adds migration `0010_round_archived`.


## v7.1.1
- Spotify previews now use Spotify's official iframe controller API.
- Moving to another voting card pauses the previous preview immediately.
- If the controller is unavailable, QueueUp removes and recreates that embed as a reliable fallback.
- No migrations are required.

## v7.1.2
- Spotify search results are disabled and grayed out when their ISRC matches a song already submitted in the same round, including single/album duplicates with different Spotify track IDs.
- The submit endpoint enforces the same ISRC-only duplicate rule server-side.
- Existing submissions are not backfilled, so the rule applies only to submissions made after this update.
- Adds migration `0012_submission_isrc`.


## QueueUp v7.2.1

- Awards two leaderboard points for each submission in a revealed round.
- Reorganizes League Control into separate searchable Rounds, Badges, and Players pages.
- Fixes inflated round submission/rating totals caused by joined aggregate multiplication.
- No database migration is required.


### QueueUp v7.2.1

- Awards 4 leaderboard points immediately for every submission in the selected season.
- Shows the bonus before the round is revealed without showing the submitted song.
- Adds clear submission-bonus text and a success confirmation.

### QueueUp v7.2.2

- Round cards in League Control now show submitted-player progress as `x/y people submitted`.
- Round cards now show completed-voter progress as `x/y people voted`.
- The denominator includes active approved league members plus active staff and superusers.
- A person counts as voted only after rating every song they were eligible to rate.
- No database migration is required.
