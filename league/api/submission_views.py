import json

from django.shortcuts import get_object_or_404

from ..models import Round
from ..realtime import broadcast
from ..services import submissions as submission_service
from ..services.rounds import player_visible_rounds
from ..services.scoring import SUBMISSION_BONUS_POINTS
from ..services.spotify_search import search_tracks
from .auth import api_methods, api_user_required
from .responses import error, success
from .serializers import round_summary, submission_track


def _round_for_player(request, pk):
    if request.user.is_staff or request.user.is_superuser:
        return get_object_or_404(Round.objects.select_related('season'), pk=pk)
    return get_object_or_404(
        player_visible_rounds().select_related('season'), pk=pk,
    )


def _json_body(request):
    try:
        value = json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


@api_methods('GET')
@api_user_required
def spotify_search(request):
    round_id = request.GET.get('round')
    round_obj = _round_for_player(request, round_id) if round_id else None
    try:
        tracks = search_tracks(request.GET.get('q', ''), round_obj)
    except Exception:
        return error(
            'spotify_unavailable',
            'Spotify search is temporarily unavailable.',
            502,
        )
    return success({'tracks': tracks})


@api_methods('GET')
@api_user_required
def submission_status(request, pk):
    round_obj = _round_for_player(request, pk)
    submission = round_obj.submissions.filter(user=request.user).first()
    return success({
        'round': round_summary(round_obj),
        'submission': submission_track(submission) if submission else None,
        'can_submit': round_obj.state == 'submitting' and submission is None,
        'submission_rules_accepted': bool(
            request.user.profile.submission_rules_accepted_at
        ),
        'submission_bonus_points': SUBMISSION_BONUS_POINTS,
    })


@api_methods('POST')
@api_user_required
def create_submission(request, pk):
    round_obj = _round_for_player(request, pk)
    body = _json_body(request)
    if body is None:
        return error('invalid_json', 'A JSON object is required.', 400)
    try:
        submission = submission_service.create_submission(
            round_obj, request.user, body.get('track_id', ''),
        )
    except submission_service.SubmissionClosed:
        return error('submissions_closed', 'Submissions are closed.', 409)
    except submission_service.InvalidTrackReference:
        return error('invalid_track', 'Invalid Spotify track.', 400)
    except submission_service.ExplicitTrack:
        return error('explicit_track', 'Explicit songs are not allowed.', 400)
    except submission_service.SubmissionRulesNotAccepted:
        return error(
            'submission_rules_required',
            'Submission rules must be accepted first.',
            403,
        )
    except submission_service.DuplicateRecording:
        return error(
            'duplicate_recording',
            'That recording has already been submitted for this round.',
            409,
        )
    except submission_service.SpotifyVerificationFailed:
        return error(
            'spotify_verification_failed',
            'That Spotify track could not be verified.',
            502,
        )
    except submission_service.SubmissionConflict:
        return error(
            'submission_conflict',
            'You already submitted, or somebody chose that song first.',
            409,
        )
    broadcast(
        'submission_added', round_id=round_obj.pk,
        submissions=round_obj.submissions.count(),
    )
    return success({
        'submission': submission_track(submission),
        'submission_bonus_points': SUBMISSION_BONUS_POINTS,
    }, status=201)
