from django.db import models
from django.utils import timezone

from ..models import Round


def player_visible_rounds(now=None):
    """Return published rounds whose public visibility time has arrived."""
    now = now or timezone.now()
    return Round.objects.filter(
        models.Q(goes_live_at__isnull=True) | models.Q(goes_live_at__lte=now),
        is_draft=False,
    )


def homepage_rounds(now=None):
    """Return the current and latest-results rounds shown on the homepage."""
    now = now or timezone.now()
    visible = player_visible_rounds(now=now).filter(archived=False)

    current = visible.filter(
        submission_opens__lte=now,
        reveal_at__gt=now,
    ).order_by('-submission_opens').first()
    if current is None:
        current = visible.filter(
            submission_opens__gt=now,
            reveal_at__gt=now,
        ).order_by('submission_opens').first()

    results = visible.filter(reveal_at__lte=now).order_by('-reveal_at').first()
    if current and results and current.pk == results.pk:
        results = None

    return current, results


def revealed_rounds_for_archive(now=None):
    """Return all publicly visible revealed rounds, including archived ones."""
    now = now or timezone.now()
    return player_visible_rounds(now=now).filter(
        reveal_at__lte=now,
    ).order_by('-reveal_at')
