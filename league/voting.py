from django.utils import timezone

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
    """Return voters who completed every vote they were eligible to cast."""
    submission_rows = list(
        Submission.objects.filter(round=round_obj).values_list('id', 'user_id')
    )
    submission_ids = {submission_id for submission_id, _user_id in submission_rows}
    own_submission_by_user = {
        user_id: submission_id for submission_id, user_id in submission_rows
    }

    votes_by_user = {}
    for voter_id, submission_id in Vote.objects.filter(round=round_obj).values_list(
        'voter_id', 'submission_id'
    ):
        if submission_id in submission_ids:
            votes_by_user.setdefault(voter_id, set()).add(submission_id)

    completed = set()
    for voter_id, voted_ids in votes_by_user.items():
        eligible_ids = submission_ids - ({own_submission_by_user[voter_id]} if voter_id in own_submission_by_user else set())
        if eligible_ids and eligible_ids.issubset(voted_ids):
            completed.add(voter_id)
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
    completed_ids = completed_voter_ids(round_obj)
    if not completed_ids:
        return set()
    return set(votes.filter(voter_id__in=completed_ids).values_list('id', flat=True))


def counted_vote_ids_for_rounds(rounds, now=None):
    now = now or timezone.now()
    counted_ids = set()
    for round_obj in rounds:
        counted_ids.update(counted_vote_ids_for_round(round_obj, now=now))
    return counted_ids
