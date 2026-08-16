from collections import Counter
from dataclasses import dataclass

from django.db.models import Avg, Count, Q
from django.utils import timezone

from ..models import Season, Vote
from ..ranking import ranked_submissions
from ..voting import counted_vote_ids_for_rounds


@dataclass(frozen=True)
class ProfileMetrics:
    submissions: object
    revealed: object
    wins: int
    podiums: int
    placements: list
    ties_for_first: int
    counted_vote_ids: set

    def as_legacy_dict(self):
        return {
            'submissions': self.submissions,
            'revealed': self.revealed,
            'wins': self.wins,
            'podiums': self.podiums,
            'placements': self.placements,
            'ties_for_first': self.ties_for_first,
            'counted_vote_ids': self.counted_vote_ids,
        }


@dataclass(frozen=True)
class ProfileReadModel:
    profile_user: object
    submissions: object
    best: object
    wins: int
    podiums: int
    round_count: int
    genres: list
    artists: list
    avg_received: object
    avg_placement: float
    win_rate: float
    seasons: object
    selected_season: object


def profile_metrics(user, season=None, now=None):
    """Return authoritative score and placement metrics for one player."""
    now = now or timezone.now()
    base_submissions = user.submissions.select_related(
        'round', 'round__season',
    ).filter(round__is_draft=False)
    if season:
        base_submissions = base_submissions.filter(round__season=season)

    related_rounds = list({
        submission.round_id: submission.round
        for submission in base_submissions
    }.values())
    counted_vote_ids = counted_vote_ids_for_rounds(related_rounds, now=now)
    counted_filter = Q(votes__id__in=counted_vote_ids)
    submissions = base_submissions.annotate(
        avg=Avg('votes__score', filter=counted_filter),
        vote_count=Count('votes', filter=counted_filter),
    )
    revealed = submissions.filter(round__reveal_at__lte=now)

    wins = 0
    podiums = 0
    ties_for_first = 0
    placements = []
    for submission in revealed:
        ranked = ranked_submissions(submission.round)
        entry = next(
            (row for row in ranked if row.item.id == submission.id),
            None,
        )
        if entry:
            placements.append(entry.place)
            wins += entry.place == 1
            podiums += entry.place <= 3
            ties_for_first += (
                entry.place == 1
                and sum(1 for row in ranked if row.place == 1) > 1
            )

    return ProfileMetrics(
        submissions=submissions,
        revealed=revealed,
        wins=wins,
        podiums=podiums,
        placements=placements,
        ties_for_first=ties_for_first,
        counted_vote_ids=counted_vote_ids,
    )


def profile_for_user(user, season_id=None, now=None):
    """Build the stable read model used by a player's profile page."""
    now = now or timezone.now()
    seasons = Season.objects.filter(
        rounds__submissions__user=user,
        rounds__reveal_at__lte=now,
    ).distinct().order_by('-starts_at')
    selected_season = (
        seasons.filter(pk=season_id).first() if season_id else None
    )
    metrics = profile_metrics(user, selected_season, now=now)
    submissions = metrics.revealed.order_by('-avg', '-submitted_at')
    genres = Counter(
        genre
        for submission in submissions
        for genre in (submission.genres or [])
    )
    artists = Counter(submission.artist for submission in submissions)
    placements = metrics.placements
    avg_received = Vote.objects.filter(
        submission__in=submissions,
        id__in=metrics.counted_vote_ids,
    ).aggregate(value=Avg('score'))['value'] or 0

    return ProfileReadModel(
        profile_user=user,
        submissions=submissions[:20],
        best=submissions.first(),
        wins=metrics.wins,
        podiums=metrics.podiums,
        round_count=submissions.count(),
        genres=genres.most_common(8),
        artists=artists.most_common(8),
        avg_received=avg_received,
        avg_placement=(
            sum(placements) / len(placements) if placements else 0
        ),
        win_rate=(
            metrics.wins / len(placements) * 100 if placements else 0
        ),
        seasons=seasons,
        selected_season=selected_season,
    )
