from django.utils import timezone
from django.db import models

from .models import Submission, Vote


def eligible_submission_ids(round_obj, user):
    return set(
        Submission.objects.filter(round=round_obj)
        .exclude(user=user)
        .values_list('id', flat=True)
    )


def voting_progress(round_obj, user):
    eligible_ids = eligible_submission_ids(round_obj, user)
    voted_ids = set(
        Vote.objects.filter(round=round_obj, voter=user, submission_id__in=eligible_ids)
        .values_list('submission_id', flat=True)
    )
    return {
        'eligible_ids': eligible_ids,
        'voted_ids': voted_ids,
        'eligible_count': len(eligible_ids),
        'voted_count': len(voted_ids),
        'complete': bool(eligible_ids) and eligible_ids.issubset(voted_ids),
        'no_votable_songs': not eligible_ids,
    }


def completed_voter_ids(round_obj):
    """Return active Django user IDs with complete ballots.

    Anonymous historical ballots are deliberately omitted from this legacy
    helper; use ``completed_voter_keys`` when calculating result eligibility.
    """
    return {
        key[1] for key in completed_voter_keys(round_obj)
        if key[0] == 'user'
    }


def completed_voter_keys(round_obj):
    """Return stable, non-merged identities with complete ballots."""
    submission_rows = list(
        Submission.objects.filter(round=round_obj).values_list(
            'id', 'user_id', 'anonymous_participant_id',
        )
    )
    submission_ids = {row[0] for row in submission_rows}
    owner_by_identity = {}
    for submission_id, user_id, anonymous_id in submission_rows:
        if user_id is not None:
            owner_by_identity[('user', user_id)] = submission_id
        elif anonymous_id is not None:
            owner_by_identity[('anonymous', anonymous_id)] = submission_id

    votes_by_user = {}
    for voter_id, anonymous_id, submission_id in Vote.objects.filter(round=round_obj).values_list(
        'voter_id', 'anonymous_participant_id', 'submission_id'
    ):
        if submission_id in submission_ids:
            key = ('user', voter_id) if voter_id is not None else ('anonymous', anonymous_id)
            if key[1] is not None:
                votes_by_user.setdefault(key, set()).add(submission_id)

    completed = set()
    for voter_key, voted_ids in votes_by_user.items():
        eligible_ids = submission_ids - ({owner_by_identity[voter_key]} if voter_key in owner_by_identity else set())
        if eligible_ids and eligible_ids.issubset(voted_ids):
            completed.add(voter_key)
    return completed


def counted_vote_ids_for_round(round_obj, now=None):
    """
    Return vote IDs that count toward results.

    While voting is still open, saved votes remain visible in live calculations.
    At and after the deadline, a voter's entire ballot counts only if they
    completed every eligible rating for that round.
    """
    now = now or timezone.now()
    votes = Vote.objects.filter(round=round_obj)
    if now < round_obj.voting_deadline:
        return set(votes.values_list('id', flat=True))
    completed_keys = completed_voter_keys(round_obj)
    if not completed_keys:
        return set()
    active_ids = [key[1] for key in completed_keys if key[0] == 'user']
    anonymous_ids = [key[1] for key in completed_keys if key[0] == 'anonymous']
    return set(votes.filter(
        models.Q(voter_id__in=active_ids) | models.Q(anonymous_participant_id__in=anonymous_ids)
    ).values_list('id', flat=True))


def counted_vote_ids_for_rounds(rounds, now=None):
    now = now or timezone.now()
    counted_ids = set()
    for round_obj in rounds:
        counted_ids.update(counted_vote_ids_for_round(round_obj, now=now))
    return counted_ids
