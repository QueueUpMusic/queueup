from django.db import transaction
from django.utils import timezone

from ..models import NotificationBlast
from ..models import PushSubscription
from ..push import send_push


def audience_for_blast(blast):
    # The first audience is intentionally only approved players, not arbitrary filters.
    return PushSubscription.objects.filter(user__is_active=True, user__profile__approved=True)


def event_key_for_blast(blast):
    return f'admin-blast:{blast.pk}'


def claim_blast(blast_id, *, due_only=False):
    """Atomically reserve a blast for one sender process."""
    with transaction.atomic():
        blast = NotificationBlast.objects.select_for_update().get(pk=blast_id)
        if blast.status not in (NotificationBlast.Status.DRAFT, NotificationBlast.Status.SCHEDULED):
            return None
        if due_only and (not blast.scheduled_for or blast.scheduled_for > timezone.now()):
            return None
        blast.status = NotificationBlast.Status.SENDING
        blast.save(update_fields=['status'])
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
            status=NotificationBlast.Status.SCHEDULED,
        )
        raise
    NotificationBlast.objects.filter(pk=blast.pk, status=NotificationBlast.Status.SENDING).update(
        status=NotificationBlast.Status.SENT, sent_at=timezone.now(),
    )
    return result


def send_now(blast, stderr=None):
    claimed = claim_blast(blast.pk)
    return send_claimed_blast(claimed, stderr) if claimed else {'sent': 0, 'skipped': 0, 'removed': 0, 'failed': 0}


def send_due_blasts(stderr=None):
    results = {'sent': 0, 'skipped': 0, 'removed': 0, 'failed': 0}
    due_ids = NotificationBlast.objects.filter(
        status=NotificationBlast.Status.SCHEDULED,
        scheduled_for__lte=timezone.now(),
    ).values_list('pk', flat=True)
    for blast_id in due_ids:
        blast = claim_blast(blast_id, due_only=True)
        if not blast:
            continue
        result = send_claimed_blast(blast, stderr)
        for key in results:
            results[key] += result[key]
    return results
