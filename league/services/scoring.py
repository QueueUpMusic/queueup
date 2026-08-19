from django.contrib.auth.models import User
from django.db import models
from django.db.models import Count, Max, Sum
from django.utils import timezone

from ..ranking import competition_rank
from ..voting import counted_vote_ids_for_rounds


SUBMISSION_BONUS_POINTS = 4


def season_leaderboard(season, now=None):
    """Return ranked players and their authoritative season point totals."""
    now = now or timezone.now()
    revealed_rounds = list(
        season.rounds.filter(reveal_at__lte=now, is_draft=False)
    )
    counted_vote_ids = counted_vote_ids_for_rounds(revealed_rounds, now=now)
    season_filter = models.Q(
        submissions__round__season=season,
        submissions__round__is_draft=False,
    )
    revealed_filter = season_filter & models.Q(
        submissions__round__reveal_at__lte=now
    )
    counted_score_filter = revealed_filter & models.Q(
        submissions__votes__id__in=counted_vote_ids
    )
    players = list(
        User.objects.annotate(
            vote_score=Sum(
                'submissions__votes__score',
                filter=counted_score_filter,
            ),
            rounds_played=Count(
                'submissions',
                filter=season_filter,
                distinct=True,
            ),
            latest_submission=Max(
                'submissions__submitted_at',
                filter=season_filter,
            ),
        ).filter(rounds_played__gt=0)
    )
    for player in players:
        player.submission_bonus = (
            player.rounds_played * SUBMISSION_BONUS_POINTS
        )
        player.total_score = (player.vote_score or 0) + player.submission_bonus
    return competition_rank(players, lambda player: player.total_score)
