from dataclasses import dataclass

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Count

from ..models import Round, Vote
from ..voting import completed_voter_ids


@dataclass(frozen=True)
class RoundParticipationReadModel:
    rows: list
    player_count: int
    submitted_count: int
    completed_count: int


def league_player_ids():
    """Return active approved players and active staff used in card totals."""
    return set(
        User.objects.filter(is_active=True).filter(
            models.Q(profile__approved=True)
            | models.Q(is_staff=True)
            | models.Q(is_superuser=True)
        ).distinct().values_list('id', flat=True)
    )


def control_rounds_with_status(query=''):
    """Return control-page rounds with authoritative participation totals."""
    rounds = Round.objects.select_related('season').annotate(
        submission_count=Count('submissions', distinct=True),
        submitted_player_count=Count('submissions__user', distinct=True),
    ).order_by('-submission_opens')
    if query:
        rounds = rounds.filter(
            models.Q(prompt__icontains=query)
            | models.Q(details__icontains=query)
            | models.Q(season__name__icontains=query)
        )

    rounds = list(rounds)
    round_ids = [round_obj.id for round_obj in rounds]
    vote_counts = {
        row['round_id']: row['count']
        for row in Vote.objects.filter(round_id__in=round_ids)
        .values('round_id')
        .annotate(count=Count('id'))
    }
    player_ids = league_player_ids()
    for round_obj in rounds:
        # Vote totals are deliberately calculated separately from submission
        # annotations so submissions x votes cannot multiply either count.
        round_obj.vote_count = vote_counts.get(round_obj.id, 0)
        round_obj.league_player_count = len(player_ids)
        round_obj.completed_voter_count = len(
            completed_voter_ids(round_obj) & player_ids
        )
    return rounds


def round_participation(round_obj):
    """Build the existing staff-only per-player round status read model."""
    submissions = list(
        round_obj.submissions.select_related('user').order_by('submitted_at')
    )
    submission_by_user = {
        submission.user_id: submission for submission in submissions
    }
    submission_ids = {submission.id for submission in submissions}

    votes_by_user = {}
    for voter_id, submission_id in Vote.objects.filter(
        round=round_obj
    ).values_list('voter_id', 'submission_id'):
        if submission_id in submission_ids:
            votes_by_user.setdefault(voter_id, set()).add(submission_id)

    participant_ids = set(submission_by_user) | set(votes_by_user)
    players = User.objects.filter(
        models.Q(profile__approved=True)
        | models.Q(is_staff=True)
        | models.Q(is_superuser=True)
        | models.Q(pk__in=participant_ids)
    ).select_related('profile').distinct().order_by('first_name', 'username')

    rows = []
    completed_count = 0
    submitted_count = 0
    for player in players:
        submission = submission_by_user.get(player.pk)
        if submission:
            submitted_count += 1
        eligible_ids = submission_ids - ({submission.id} if submission else set())
        voted_ids = votes_by_user.get(player.pk, set()) & eligible_ids
        voting_complete = bool(eligible_ids) and eligible_ids.issubset(voted_ids)
        if voting_complete:
            completed_count += 1
        rows.append({
            'player': player,
            'submission': submission,
            'voted_count': len(voted_ids),
            'eligible_count': len(eligible_ids),
            'voting_complete': voting_complete,
            'voting_started': bool(voted_ids),
        })

    return RoundParticipationReadModel(
        rows=rows,
        player_count=len(rows),
        submitted_count=submitted_count,
        completed_count=completed_count,
    )
