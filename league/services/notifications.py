from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models

from ..models import AchievementUnlock, PushSubscription, Submission, Season
from .recaps import recap_eligible_user_ids
from .achievements import earned_badges
from .ballots import ballot_for_user


REMINDER_WINDOW = timedelta(hours=6)
RECAP_NOTIFICATION_WINDOW = timedelta(hours=6)


class InvalidPushSubscription(Exception):
    pass


def register_push_subscription(user, data):
    try:
        keys = data['keys']
        endpoint = data['endpoint']
        p256dh = keys['p256dh']
        auth = keys['auth']
    except (KeyError, TypeError):
        raise InvalidPushSubscription
    subscription, _ = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={'user': user, 'p256dh': p256dh, 'auth': auth},
    )
    return subscription


def remove_push_subscription(user, endpoint):
    deleted, _ = PushSubscription.objects.filter(
        user=user, endpoint=endpoint,
    ).delete()
    return bool(deleted)


@dataclass(frozen=True)
class PushNotificationEvent:
    subscriptions: object
    event_key: str
    title: str
    body: str
    url: str


@dataclass(frozen=True)
class UserNotificationEvent:
    user: object
    event_key: str
    title: str
    body: str
    url: str
    unlock: AchievementUnlock


def global_notification_audience():
    """Return the existing active, approved-or-staff push audience."""
    return PushSubscription.objects.filter(
        models.Q(user__is_staff=True) | models.Q(user__profile__approved=True),
        user__is_active=True,
    )


def submission_reminder_audience(round_obj):
    """Return active subscribers who have not submitted to this round."""
    submitted_ids = Submission.objects.filter(round=round_obj).values_list(
        'user_id', flat=True,
    )
    return PushSubscription.objects.filter(user__is_active=True).exclude(
        user_id__in=submitted_ids,
    )


def voting_reminder_audience(round_obj):
    """Return approved subscribers whose authoritative ballot is incomplete."""
    incomplete_ids = []
    users = User.objects.filter(is_active=True, profile__approved=True)
    for user in users.iterator():
        ballot = ballot_for_user(round_obj, user)
        if not ballot.complete and not ballot.no_votable_songs:
            incomplete_ids.append(user.id)
    return PushSubscription.objects.filter(user_id__in=incomplete_ids)


def round_notification_events(round_obj, now):
    """Return the currently due round push events in their existing order."""
    if not round_obj.is_visible:
        return []

    round_url = f'/round/{round_obj.pk}/'
    audience = global_notification_audience
    events = [PushNotificationEvent(
        audience(),
        f'round:{round_obj.pk}:live',
        'New round is live',
        f'“{round_obj.prompt}” is opening soon. Check the prompt and deadlines.',
        round_url,
    )]

    if round_obj.submission_opens <= now < round_obj.submission_deadline:
        events.append(PushNotificationEvent(
            audience(),
            f'round:{round_obj.pk}:submissions-open',
            'Submit your song',
            round_obj.prompt,
            round_url,
        ))

    if (
        round_obj.submission_deadline - REMINDER_WINDOW
        <= now
        < round_obj.submission_deadline
    ):
        events.append(PushNotificationEvent(
            submission_reminder_audience(round_obj),
            f'round:{round_obj.pk}:submission-reminder-6h',
            '6 hours left to submit',
            f'Choose a clean song for “{round_obj.prompt}” before submissions close.',
            round_url,
        ))

    if round_obj.submission_deadline <= now < round_obj.voting_deadline:
        events.append(PushNotificationEvent(
            audience(),
            f'round:{round_obj.pk}:voting-open',
            'Voting open',
            f'Rate the songs for “{round_obj.prompt}”.',
            round_url,
        ))

    if (
        round_obj.voting_deadline - REMINDER_WINDOW
        <= now
        < round_obj.voting_deadline
    ):
        events.append(PushNotificationEvent(
            voting_reminder_audience(round_obj),
            f'round:{round_obj.pk}:voting-reminder-6h',
            '6 hours left to vote',
            f'Finish rating the songs for “{round_obj.prompt}”.',
            round_url,
        ))

    if round_obj.reveal_at <= now:
        events.append(PushNotificationEvent(
            audience(),
            f'round:{round_obj.pk}:results',
            'Results ready',
            f'See who won “{round_obj.prompt}”.',
            round_url,
        ))

    return events


def achievement_notification_events():
    """Yield earned-achievement pushes for the existing approved audience."""
    users = User.objects.filter(is_active=True, profile__approved=True)
    for user in users.iterator():
        for badge in earned_badges(user):
            if not badge['earned']:
                continue
            unlock, _ = AchievementUnlock.objects.get_or_create(
                user=user,
                key=badge['key'],
            )
            yield UserNotificationEvent(
                user,
                f'achievement:{unlock.pk}',
                'Badge unlocked',
                badge['description'],
                f'/stats/{user.username}/',
                unlock,
            )


def recap_notification_events(now):
    """Yield recap pushes only when a season's recap has recently become available."""
    cutoff = now - RECAP_NOTIFICATION_WINDOW
    seasons = Season.objects.filter(ends_at__lte=now).filter(
        models.Q(ends_at__gt=cutoff)
        | models.Q(rounds__reveal_at__gt=cutoff, rounds__reveal_at__lte=now)
    ).distinct().order_by('id')
    for season in seasons:
        for user in User.objects.filter(
            id__in=recap_eligible_user_ids(season, now=now), is_active=True,
            profile__approved=True,
        ).iterator():
            yield PushNotificationEvent(
                PushSubscription.objects.filter(user=user),
                f'season:{season.pk}:recap:{user.pk}',
                'Your QueueUp Season Recap is ready',
                'Check out your season recap.',
                f'/seasons/{season.pk}/recap/',
            )
