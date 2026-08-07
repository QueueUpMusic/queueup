from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone

from league.achievements import earned_badges
from league.models import AchievementUnlock, PushSubscription, Round, Submission
from league.push import send_push, send_user_push
from league.voting import voting_progress


class Command(BaseCommand):
    help = 'Send due QueueUp web-push notifications using global per-device subscriptions.'

    def send(self, subscriptions, event_key, title, body, url='/home/'):
        return send_push(subscriptions, event_key, title, body, url, self.stderr)

    def send_all(self, event_key, title, body, url='/home/'):
        audience = PushSubscription.objects.filter(models.Q(user__is_staff=True) | models.Q(user__profile__approved=True), user__is_active=True)
        return self.send(audience, event_key, title, body, url)

    def send_user(self, user, event_key, title, body, url='/stats/'):
        return send_user_push(user, event_key, title, body, url, self.stderr)

    def handle(self, *args, **options):
        now = timezone.now()
        reminder_window = timedelta(hours=6)
        summary = {'sent': 0, 'skipped': 0, 'removed': 0, 'failed': 0}

        def merge(result):
            for key in summary:
                summary[key] += result[key]

        for rnd in Round.objects.select_related('season').all():
            if not rnd.is_visible:
                continue
            round_url = f'/round/{rnd.pk}/'
            merge(self.send_all(
                f'round:{rnd.pk}:live',
                'New round is live',
                f'“{rnd.prompt}” is opening soon. Check the prompt and deadlines.',
                round_url,
            ))

            if rnd.submission_opens <= now < rnd.submission_deadline:
                merge(self.send_all(f'round:{rnd.pk}:submissions-open', 'Submit your song', rnd.prompt, round_url))

            if (rnd.submission_deadline - reminder_window) <= now < rnd.submission_deadline:
                submitted_ids = Submission.objects.filter(round=rnd).values_list('user_id', flat=True)
                subscriptions = PushSubscription.objects.filter(user__is_active=True).exclude(user_id__in=submitted_ids)
                merge(self.send(
                    subscriptions,
                    f'round:{rnd.pk}:submission-reminder-6h',
                    '6 hours left to submit',
                    f'Choose a clean song for “{rnd.prompt}” before submissions close.',
                    round_url,
                ))

            if rnd.submission_deadline <= now < rnd.voting_deadline:
                merge(self.send_all(f'round:{rnd.pk}:voting-open', 'Voting open', f'Rate the songs for “{rnd.prompt}”.', round_url))

            if (rnd.voting_deadline - reminder_window) <= now < rnd.voting_deadline:
                incomplete_ids = []
                for user in User.objects.filter(is_active=True, profile__approved=True).iterator():
                    progress = voting_progress(rnd, user)
                    if not progress['complete'] and not progress['no_votable_songs']:
                        incomplete_ids.append(user.id)
                subscriptions = PushSubscription.objects.filter(user_id__in=incomplete_ids)
                merge(self.send(
                    subscriptions,
                    f'round:{rnd.pk}:voting-reminder-6h',
                    '6 hours left to vote',
                    f'Finish rating the songs for “{rnd.prompt}”.',
                    round_url,
                ))

            if rnd.reveal_at <= now:
                merge(self.send_all(f'round:{rnd.pk}:results', 'Results ready', f'See who won “{rnd.prompt}”.', round_url))

        for user in User.objects.filter(is_active=True, profile__approved=True).iterator():
            for badge in earned_badges(user):
                if not badge['earned']:
                    continue
                unlock, _ = AchievementUnlock.objects.get_or_create(user=user, key=badge['key'])
                result = self.send_user(user, f'achievement:{unlock.pk}', 'Badge unlocked', badge['description'], f'/stats/{user.username}/')
                merge(result)
                if result['sent'] and not unlock.notification_sent_at:
                    unlock.notification_sent_at = now
                    unlock.save(update_fields=['notification_sent_at'])

        self.stdout.write(self.style.SUCCESS(
            'QueueUp notification check complete: '
            f"sent={summary['sent']} skipped={summary['skipped']} "
            f"removed={summary['removed']} failed={summary['failed']}"
        ))
