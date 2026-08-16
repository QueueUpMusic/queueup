from dataclasses import dataclass
from datetime import timedelta

from django.db import models
from django.utils import timezone

from ..models import Round
from ..ranking import ranked_submissions
from .ballots import BallotReadModel, ballot_for_user


@dataclass(frozen=True)
class RoundDetailReadModel:
    mine: object
    submissions: list
    ranked_results: list
    ranking_by_id: dict
    winners: list
    ballot: BallotReadModel
    show_voting_guide: bool


class UnknownRoundAction(Exception):
    pass


class RoundTransitionNotAllowed(Exception):
    pass


class RoundNotRevealed(Exception):
    pass


def round_form_data_for_action(data, action, now=None):
    """Apply the existing save-action timestamp override to round form data."""
    if action == 'publish':
        now = now or timezone.now()
        data['goes_live_at'] = timezone.localtime(now).strftime(
            '%Y-%m-%dT%H:%M:%S'
        )
    return data


def save_round(round_obj, action):
    """Persist the existing draft/save/publish state selected by staff."""
    round_obj.is_draft = action == 'draft'
    round_obj.save()
    return round_obj


def apply_round_action(round_obj, action, now=None):
    """Apply a valid manual lifecycle transition without losing history."""
    now = now or timezone.now()
    allowed_states = {
        'open_submissions': {'upcoming', 'submitting'},
        'open_voting': {'submitting', 'voting'},
        'lock_voting': {'voting', 'locked'},
        'reveal': {'voting', 'locked', 'revealed'},
    }
    if action not in allowed_states:
        raise UnknownRoundAction
    if round_obj.state not in allowed_states[action]:
        raise RoundTransitionNotAllowed

    if action == 'open_submissions':
        round_obj.submission_opens = now
        if round_obj.submission_deadline <= now:
            round_obj.submission_deadline = now + timedelta(days=3)
    elif action == 'open_voting':
        round_obj.submission_deadline = now
        if round_obj.voting_deadline <= now:
            round_obj.voting_deadline = now + timedelta(days=2)
    elif action == 'lock_voting':
        round_obj.voting_deadline = now
        if round_obj.reveal_at <= now:
            round_obj.reveal_at = now + timedelta(minutes=5)
    elif action == 'reveal':
        if round_obj.voting_deadline > now:
            round_obj.voting_deadline = now
        round_obj.reveal_at = now
    round_obj.save()
    return round_obj


def archive_round(round_obj):
    """Archive a completed round without altering its league history."""
    if round_obj.state != 'revealed':
        raise RoundNotRevealed
    round_obj.archived = True
    round_obj.save(update_fields=['archived'])
    return round_obj


def round_detail_for_user(round_obj, user):
    """Build the read model used to display a round to a league member."""
    ranked = ranked_submissions(round_obj)
    return RoundDetailReadModel(
        mine=round_obj.submissions.filter(user=user).first(),
        submissions=[entry.item for entry in ranked],
        ranked_results=ranked,
        ranking_by_id={entry.item.id: entry for entry in ranked},
        winners=[entry.item for entry in ranked if entry.place == 1],
        ballot=ballot_for_user(round_obj, user),
        show_voting_guide=(
            round_obj.state == 'voting'
            and not user.profile.voting_guide_seen
        ),
    )


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
