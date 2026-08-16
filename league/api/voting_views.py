import json

from django.shortcuts import get_object_or_404

from ..models import Round, Submission
from ..realtime import broadcast
from ..services import votes as vote_service
from ..services.ballots import ballot_for_user
from ..services.rounds import player_visible_rounds
from .auth import api_methods, api_user_required
from .responses import error, success


def _round_for_player(request, pk):
    if request.user.is_staff or request.user.is_superuser:
        return get_object_or_404(Round, pk=pk)
    return get_object_or_404(player_visible_rounds(), pk=pk)


@api_methods('POST')
@api_user_required
def save_vote(request, pk, submission_id):
    round_obj = _round_for_player(request, pk)
    submission = get_object_or_404(
        Submission, pk=submission_id, round=round_obj,
    )
    try:
        body = json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return error('invalid_json', 'A JSON object is required.', 400)
    if not isinstance(body, dict):
        return error('invalid_json', 'A JSON object is required.', 400)
    try:
        result = vote_service.record_vote(
            round_obj, request.user, submission, body.get('score'),
        )
    except vote_service.VotingClosed:
        return error('voting_closed', 'Voting is not open.', 409)
    except vote_service.SelfVoteNotAllowed:
        return error('self_vote', 'You cannot vote for your own song.', 403)
    except vote_service.SubmissionRoundMismatch:
        return error('submission_round_mismatch', 'Invalid submission.', 400)
    except vote_service.InvalidVoteScore:
        return error('invalid_score', 'Score must be an integer from 1 to 5.', 400)

    ballot = ballot_for_user(round_obj, request.user)
    broadcast(
        'vote_saved', round_id=round_obj.pk,
        votes=round_obj.votes.count(),
    )
    return success({
        'vote': {
            'id': result.vote.pk,
            'submission_id': submission.pk,
            'score': result.vote.score,
            'created': result.created,
        },
        'ballot': {
            'saved_scores': ballot.vote_scores,
            'eligible_count': ballot.eligible_count,
            'voted_count': ballot.voted_count,
            'complete': ballot.complete,
            'no_votable_songs': ballot.no_votable_songs,
        },
    })
