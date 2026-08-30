from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import NotificationBlast
from ..models import PushSubscription
from ..push import send_push


SENDING_LEASE = timedelta(minutes=15)


def audience_for_blast(blast):
    # The first audience is intentionally only approved players, not arbitrary filters.
    return PushSubscription.objects.filter(user__is_active=True, user__profile__approved=True)


def event_key_for_blast(blast):
    return f'admin-blast:{blast.pk}'


def claim_blast(blast_id, *, due_only=False):
    """Atomically reserve a blast for one sender process."""
    with transaction.atomic():
        blast = NotificationBlast.objects.select_for_update().get(pk=blast_id)
        now = timezone.now()
        stale_sending = (
            blast.status == NotificationBlast.Status.SENDING
            and (not blast.sending_started_at or blast.sending_started_at <= now - SENDING_LEASE)
        )
        if blast.status not in (NotificationBlast.Status.DRAFT, NotificationBlast.Status.SCHEDULED) and not stale_sending:
            return None
        if due_only and (not blast.scheduled_for or blast.scheduled_for > now):
            return None
        blast.status = NotificationBlast.Status.SENDING
        blast.sending_started_at = now
        blast.save(update_fields=['status', 'sending_started_at'])
        return blast


def send_claimed_blast(blast, stderr=None):
    try:
        result = send_push(
            audience_for_blast(blast), event_key_for_blast(blast),
            blast.title, blast.body, blast.destination, stderr,
        )
    except Exception:
        # Delivery records make a retry safe even if a process dies after some
        # devices were handled but before the blast is marked sent.
        NotificationBlast.objects.filter(pk=blast.pk, status=NotificationBlast.Status.SENDING).update(
            status=NotificationBlast.Status.SCHEDULED, sending_started_at=None,
        )
        raise
    NotificationBlast.objects.filter(pk=blast.pk, status=NotificationBlast.Status.SENDING).update(
        status=NotificationBlast.Status.SENT, sending_started_at=None, sent_at=timezone.now(),
    )
    return result


def send_now(blast, stderr=None):
    claimed = claim_blast(blast.pk)
    return send_claimed_blast(claimed, stderr) if claimed else {'sent': 0, 'skipped': 0, 'removed': 0, 'failed': 0}


def send_due_blasts(stderr=None):
    results = {'sent': 0, 'skipped': 0, 'removed': 0, 'failed': 0}
    now = timezone.now()
    due_ids = NotificationBlast.objects.filter(
        Q(status=NotificationBlast.Status.SCHEDULED)
        | Q(status=NotificationBlast.Status.SENDING, sending_started_at__lte=now - SENDING_LEASE)
        | Q(status=NotificationBlast.Status.SENDING, sending_started_at__isnull=True),
        scheduled_for__lte=now,
    ).values_list('pk', flat=True)
    for blast_id in due_ids:
        blast = claim_blast(blast_id, due_only=True)
        if not blast:
            continue
        result = send_claimed_blast(blast, stderr)
        for key in results:
            results[key] += result[key]
    return results
