from dataclasses import dataclass

from ..models import Vote
from ..voting import voting_progress


@dataclass(frozen=True)
class BallotReadModel:
    eligible_ids: set
    voted_ids: set
    vote_scores: dict
    eligible_count: int
    voted_count: int
    complete: bool
    no_votable_songs: bool


def ballot_for_user(round_obj, user):
    """Return the saved ratings and progress for a user's round ballot."""
    progress = voting_progress(round_obj, user)
    vote_scores = dict(
        Vote.objects.filter(round=round_obj, voter=user)
        .values_list('submission_id', 'score')
    )
    return BallotReadModel(
        eligible_ids=progress['eligible_ids'],
        voted_ids=progress['voted_ids'],
        vote_scores=vote_scores,
        eligible_count=progress['eligible_count'],
        voted_count=progress['voted_count'],
        complete=progress['complete'],
        no_votable_songs=progress['no_votable_songs'],
    )
