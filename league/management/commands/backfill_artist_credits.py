import time

from django.core.management.base import BaseCommand

from league.models import Submission
from league.spotify import api_get, normalize_track


class Command(BaseCommand):
    help = 'Backfill structured Spotify artist credits for legacy submissions.'

    def add_arguments(self, parser):
        parser.add_argument('--sleep', type=float, default=0.2, dest='sleep_seconds')
        parser.add_argument('--limit', type=int, default=None)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        queryset = Submission.objects.filter(artist_credits=[]).order_by('pk')
        if options['limit'] is not None:
            queryset = queryset[:options['limit']]

        updated = unresolved = 0
        for submission in queryset.iterator() if options['limit'] is None else queryset:
            try:
                track = normalize_track(api_get(f'/tracks/{submission.spotify_track_id}'))
                credits = track.get('artist_credits') or []
                if not credits:
                    raise ValueError('Spotify track response did not contain structured artist credits')
                if not options['dry_run']:
                    submission.artist_credits = credits
                    submission.artist_ids = [credit['id'] for credit in credits]
                    submission.save(update_fields=['artist_credits', 'artist_ids'])
                updated += 1
            except Exception as exc:
                unresolved += 1
                self.stderr.write(
                    f'Unresolved submission {submission.pk} '
                    f'(track {submission.spotify_track_id!r}): {exc}'
                )
            finally:
                if options['sleep_seconds'] > 0:
                    time.sleep(options['sleep_seconds'])

        action = 'would update' if options['dry_run'] else 'updated'
        self.stdout.write(
            self.style.SUCCESS(
                f'{action} {updated} submission(s); '
                f'{unresolved} unresolved (no display-name guessing performed).'
            )
        )
