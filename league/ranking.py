from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Avg, Count, Q

from .voting import counted_vote_ids_for_round


@dataclass(frozen=True)
class RankedItem:
    item: object
    score: Decimal
    place: int
    tied: bool

    @property
    def label(self):
        suffix = 'th' if 10 <= self.place % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(self.place % 10, 'th')
        return f"{'Tied for ' if self.tied else ''}{self.place}{suffix}"


def exact_score(value):
    if value is None:
        return Decimal('0')
    return value if isinstance(value, Decimal) else Decimal(str(value))


def competition_rank(items, score_getter=lambda item: item.score):
    ordered = sorted(items, key=lambda item: exact_score(score_getter(item)), reverse=True)
    counts = {}
    for item in ordered:
        score = exact_score(score_getter(item))
        counts[score] = counts.get(score, 0) + 1
    ranked = []
    previous_score = None
    place = 0
    for index, item in enumerate(ordered, start=1):
        score = exact_score(score_getter(item))
        if score != previous_score:
            place = index
            previous_score = score
        ranked.append(RankedItem(item=item, score=score, place=place, tied=counts[score] > 1))
    return ranked


def ranked_submissions(round_obj):
    counted_vote_ids = counted_vote_ids_for_round(round_obj)
    counted_filter = Q(votes__id__in=counted_vote_ids)
    submissions = list(
        round_obj.submissions.select_related('user').annotate(
            avg=Avg('votes__score', filter=counted_filter),
            vote_count=Count('votes', filter=counted_filter),
        )
    )
    return competition_rank(submissions, lambda submission: submission.avg)


def winner_ids(round_obj):
    ranked = ranked_submissions(round_obj)
    return {entry.item.id for entry in ranked if entry.place == 1}
