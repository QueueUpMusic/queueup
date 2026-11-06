# Website account deletion

QueueUp provides permanent website deletion at `/account/delete/`. The route is session-authenticated, works for pending and approved accounts, requires the current username, password, and exact `DELETE` phrase, and accepts only POST for the destructive action.

## What is removed

The Django user, password hash, email, display name, profile and picture, Spotify connection, web/native notification registrations and deliveries, onboarding records, badges, achievement unlocks, and other user-owned records are deleted. All sessions for the account are invalidated before the request completes. Profile-picture storage deletion runs after the database transaction commits.

## What is retained

Historical songs and ratings needed to preserve league history remain in their original rounds. Each deleted account receives a separate `AnonymousParticipant` row with no username, email, token, or relationship to the deleted user. Submission ownership and vote ownership become nullable and point to that private non-authenticatable identity; presentation exposes only `deleted-user`. Anonymous identities are never included in active profiles, achievements, or season leaderboards.

Completed-ballot calculations use the anonymous identity, so a complete historical ballot remains complete and an incomplete ballot remains incomplete. Anonymous ratings continue to affect the same historical song averages that they affected before deletion. Deleted submissions retain their historical ranking/result data but do not earn a personal four-point submission bonus after deletion.

## Deployment

Build and migrate the web service, then run the normal checks:

```bash
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py collectstatic --noinput
```

No mobile repository or production data was changed by this implementation.

## Rollback considerations

Migration `0022_anonymousparticipant_and_more` is additive plus nullable foreign-key changes. Do not roll it back after deletions have created anonymous historical identities; that would require choosing how to restore deleted users and would destroy the intended deletion semantics. Roll forward with a corrective migration if needed. The existing database backup remains the recovery path for an accidental deletion, although the product flow intentionally provides no account recovery.

## Verification

`league.tests_account_deletion` covers direct unauthenticated redirect, pending access, confirmation failures, personal-record removal, session invalidation, complete and incomplete historical ballots, and anonymous API results. The full suite also surfaced two existing unrelated issues: a recap test creates a duplicate same-user/same-round submission before moving it, and that test should create the row directly in its target round.
