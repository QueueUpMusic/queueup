from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404

from ..models import Round, Season
from ..services.achievements import earned_badges, prestige_badges
from ..services.profiles import profile_for_user
from ..services.rounds import (
    homepage_rounds,
    player_visible_rounds,
    revealed_rounds_for_archive,
    round_detail_for_user,
)
from ..services.scoring import season_leaderboard
from .auth import api_methods, api_user_required
from .responses import error, success
from .serializers import (
    badge_summary,
    revealed_submission,
    round_summary,
    season_summary,
    submission_track,
    user_summary,
)


def _player_round(request, pk):
    if request.user.is_staff or request.user.is_superuser:
        return get_object_or_404(Round.objects.select_related('season'), pk=pk)
    return get_object_or_404(
        player_visible_rounds().select_related('season'), pk=pk,
    )


@api_methods('GET')
@api_user_required
def dashboard(request):
    current, results = homepage_rounds()
    cards = []
    if current and current.state != 'upcoming':
        cards.append({'kind': 'current', 'round': round_summary(current)})
    if results:
        cards.append({'kind': 'results', 'round': round_summary(results)})
    if current and current.state == 'upcoming':
        cards.append({'kind': 'current', 'round': round_summary(current)})
    mine = None
    if current:
        submission = current.submissions.filter(user=request.user).first()
        if submission:
            mine = submission_track(submission)
    return success({
        'cards': cards,
        'current_round': round_summary(current) if current else None,
        'results_round': round_summary(results) if results else None,
        'my_submission': mine,
    })


@api_methods('GET')
@api_user_required
def seasons(request):
    rows = Season.objects.order_by('-starts_at', '-id')
    return success({'seasons': [season_summary(row) for row in rows]})


@api_methods('GET')
@api_user_required
def archive(request):
    return success({
        'rounds': [
            round_summary(round_obj)
            for round_obj in revealed_rounds_for_archive()
        ],
    })


@api_methods('GET')
@api_user_required
def round_detail(request, pk):
    round_obj = _player_round(request, pk)
    detail = round_detail_for_user(round_obj, request.user)
    eligible = round_obj.submissions.filter(
        pk__in=detail.ballot.eligible_ids,
    ).order_by('submitted_at', 'id')
    data = {
        'round': round_summary(round_obj),
        'my_submission': (
            submission_track(detail.mine) if detail.mine else None
        ),
        'ballot': {
            'eligible_submissions': [submission_track(row) for row in eligible],
            'saved_scores': detail.ballot.vote_scores,
            'eligible_count': detail.ballot.eligible_count,
            'voted_count': detail.ballot.voted_count,
            'complete': detail.ballot.complete,
            'no_votable_songs': detail.ballot.no_votable_songs,
        },
        'show_voting_guide': detail.show_voting_guide,
    }
    if round_obj.state == 'revealed':
        data['results'] = [
            revealed_submission(entry.item, entry)
            for entry in detail.ranked_results
        ]
    return success(data)


@api_methods('GET')
@api_user_required
def ballot(request, pk):
    round_obj = _player_round(request, pk)
    detail = round_detail_for_user(round_obj, request.user)
    eligible = round_obj.submissions.filter(
        pk__in=detail.ballot.eligible_ids,
    ).order_by('submitted_at', 'id')
    return success({
        'round_id': round_obj.pk,
        'eligible_submissions': [submission_track(row) for row in eligible],
        'saved_scores': detail.ballot.vote_scores,
        'eligible_count': detail.ballot.eligible_count,
        'voted_count': detail.ballot.voted_count,
        'complete': detail.ballot.complete,
        'no_votable_songs': detail.ballot.no_votable_songs,
    })


@api_methods('GET')
@api_user_required
def results(request, pk):
    round_obj = _player_round(request, pk)
    if round_obj.state != 'revealed':
        return error('results_unavailable', 'Results are not available.', 404)
    detail = round_detail_for_user(round_obj, request.user)
    return success({
        'round': round_summary(round_obj),
        'results': [
            revealed_submission(entry.item, entry)
            for entry in detail.ranked_results
        ],
    })


@api_methods('GET')
@api_user_required
def leaderboard(request):
    seasons = Season.objects.order_by('-starts_at', '-id')
    season_id = request.GET.get('season')
    selected = seasons.filter(pk=season_id).first() if season_id else None
    selected = selected or seasons.filter(active=True).first() or seasons.first()
    rows = []
    if selected:
        rows = [{
            'place': entry.place,
            'tied': entry.tied,
            'player': user_summary(entry.item),
            'vote_score': entry.item.vote_score or 0,
            'submission_bonus': entry.item.submission_bonus,
            'rounds_played': entry.item.rounds_played,
            'total_score': entry.item.total_score,
        } for entry in season_leaderboard(selected)]
    return success({
        'season': season_summary(selected) if selected else None,
        'seasons': [season_summary(row) for row in seasons],
        'leaderboard': rows,
    })


@api_methods('GET')
@api_user_required
def profile(request, username):
    profile_user = get_object_or_404(User, username=username)
    read = profile_for_user(
        profile_user,
        season_id=request.GET.get('season'),
    )
    return success({
        'player': user_summary(profile_user),
        'metrics': {
            'wins': read.wins,
            'podiums': read.podiums,
            'round_count': read.round_count,
            'average_received': float(read.avg_received),
            'average_placement': read.avg_placement,
            'win_rate': read.win_rate,
        },
        'favorite_genres': read.genres,
        'most_submitted_artists': read.artists,
        'history': [revealed_submission(row) for row in read.submissions],
        'season': (
            season_summary(read.selected_season)
            if read.selected_season else None
        ),
        'seasons': [season_summary(row) for row in read.seasons],
        'badges': [badge_summary(row) for row in earned_badges(profile_user)],
        'prestige_badges': [
            {
                'id': badge.pk,
                'name': badge.name,
                'description': badge.description,
                'icon': badge.icon,
            }
            for badge in prestige_badges(profile_user)
        ],
    })


@api_methods('GET')
@api_user_required
def achievements(request, username):
    profile_user = get_object_or_404(User, username=username)
    return success({
        'player': user_summary(profile_user),
        'badges': [badge_summary(row) for row in earned_badges(profile_user)],
        'prestige_badges': [
            {
                'id': badge.pk,
                'name': badge.name,
                'description': badge.description,
                'icon': badge.icon,
            }
            for badge in prestige_badges(profile_user)
        ],
    })
