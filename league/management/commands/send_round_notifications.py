from django.core.management.base import BaseCommand
from django.utils import timezone

from league.models import Round
from league.push import send_push, send_user_push
from league.services.notifications import (
    achievement_notification_events,
    global_notification_audience,
    recap_notification_events,
    round_notification_events,
    native_audience_for_round_event,
)
from league.services.notification_blasts import send_due_blasts
from league.services.native_push import send_native_push
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'Send due QueueUp web and native push notifications.'

    def send(self, subscriptions, event_key, title, body, url='/home/'):
        return send_push(subscriptions, event_key, title, body, url, self.stderr)

    def send_all(self, event_key, title, body, url='/home/'):
        audience = global_notification_audience()
        return self.send(audience, event_key, title, body, url)

    def send_user(self, user, event_key, title, body, url='/stats/'):
        return send_user_push(user, event_key, title, body, url, self.stderr)

    def send_native(self, users, event_key, title, body, url='/home/'):
        try:
            return send_native_push(users, event_key, title, body, url, stderr=self.stderr)
        except Exception as exc:
            self.stderr.write(f'Native push delivery failed: {str(exc)[:200]}\n')
            return {'sent': 0, 'skipped': 0, 'removed': 0, 'failed': 0}

    def handle(self, *args, **options):
        now = timezone.now()
        summary = {'sent': 0, 'skipped': 0, 'removed': 0, 'failed': 0}

        def merge(result):
            for key in summary:
                summary[key] += result[key]

        for rnd in Round.objects.select_related('season').all():
            for event in round_notification_events(rnd, now):
                merge(self.send(
                    event.subscriptions,
                    event.event_key,
                    event.title,
                    event.body,
                    event.url,
                ))
                merge(self.send_native(native_audience_for_round_event(rnd, event.event_key), event.event_key, event.title, event.body, event.url))

        for event in achievement_notification_events():
            result = self.send_user(
                event.user,
                event.event_key,
                event.title,
                event.body,
                event.url,
            )
            merge(result)
            native = self.send_native(event.user, event.event_key, event.title, event.body, event.url)
            merge(native)
            if result['sent'] and not event.unlock.notification_sent_at:
                event.unlock.notification_sent_at = now
                event.unlock.save(update_fields=['notification_sent_at'])

        for event in recap_notification_events(now):
            merge(self.send(event.subscriptions, event.event_key, event.title, event.body, event.url))
            season_id = event.event_key.split(':')[1]
            native_url = f'/season/{season_id}/recap'
            user = User.objects.filter(pk=event.event_key.rsplit(':', 1)[-1])
            merge(self.send_native(user, event.event_key, event.title, event.body, native_url))

        merge(send_due_blasts(self.stderr))

        self.stdout.write(self.style.SUCCESS(
            'QueueUp notification check complete: '
            f"sent={summary['sent']} skipped={summary['skipped']} "
            f"removed={summary['removed']} failed={summary['failed']}"
        ))
