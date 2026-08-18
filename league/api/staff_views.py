import base64
import io
import json
import qrcode
from django.conf import settings
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.urls import reverse

from ..forms import BadgeForm, RoundForm, SeasonForm
from ..models import Badge, Round, Season, SpotifyConnection
from ..push import send_user_push
from ..realtime import broadcast
from ..services import badges as badge_service
from ..services import membership as membership_service
from ..services import round_status as round_status_service
from ..services import rounds as round_service
from ..spotify import user_api
from ..services import staff as staff_service
from .auth import api_methods, api_staff_required
from .responses import error, success


@api_methods('POST')
@api_staff_required
def create_playlist(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    connection = SpotifyConnection.objects.filter(user=request.user).first()
    if not connection:
        return error('spotify_not_connected', 'Connect your Spotify account first.', 403)
    try:
        playlist = user_api(connection, 'POST', '/me/playlists', {
            'name': f'{rnd.season.name} · {rnd.prompt}'[:100],
            'description': 'Created by QueueUp',
            'public': False
        })
        uris = list(rnd.submissions.order_by('submitted_at').values_list('spotify_uri', flat=True))
        if uris:
            user_api(connection, 'POST', f"/playlists/{playlist['id']}/items", {'uris': uris})
        rnd.playlist_url = playlist['external_urls']['spotify']
        rnd.save(update_fields=['playlist_url'])
        return success({'playlist_url': rnd.playlist_url})
    except Exception as exc:
        return error('spotify_api_failed', str(exc), 502)


def _round(value):
    return {
        'id': value.pk, 'season_id': value.season_id,
        'season': value.season.name, 'prompt': value.prompt,
        'details': value.details, 'state': value.state,
        'is_draft': value.is_draft, 'archived': value.archived,
        'submission_count': getattr(value, 'submission_count', 0),
        'vote_count': getattr(value, 'vote_count', 0),
        'submitted_player_count': getattr(value, 'submitted_player_count', 0),
        'completed_voter_count': getattr(value, 'completed_voter_count', 0),
        'league_player_count': getattr(value, 'league_player_count', 0),
        'playlist_url': value.playlist_url,
    }


@api_methods('GET')
@api_staff_required
def overview(request):
    signup_url = f"{settings.PUBLIC_URL}{reverse('signup')}"
    qr = qrcode.make(signup_url)
    out = io.BytesIO()
    qr.save(out, format='PNG')
    qr_data = base64.b64encode(out.getvalue()).decode()

    spotify_connection = SpotifyConnection.objects.filter(user=request.user).first()

    return success({
        'round_count': Round.objects.count(),
        'badge_count': Badge.objects.count(),
        'season_count': Season.objects.count(),
        'user_count': User.objects.count(),
        'signup_url': signup_url,
        'signup_qr': qr_data,
        'spotify_connection': {
            'display_name': spotify_connection.display_name,
            'spotify_user_id': spotify_connection.spotify_user_id,
        } if spotify_connection else None,
    })


@api_methods('GET')
@api_staff_required
def rounds(request):
    query = request.GET.get('q', '').strip()
    return success({'query': query, 'rounds': [
        _round(value)
        for value in round_status_service.control_rounds_with_status(query)
    ]})


@api_methods('POST', 'PATCH')
@api_staff_required
def round_save(request, pk=None):
    rnd = get_object_or_404(Round, pk=pk) if pk else None
    try:
        data = json.loads(request.body)
    except ValueError:
        return error('invalid_json', 'Invalid JSON body.', 400)

    action = data.get('save_action')
    data = round_service.round_form_data_for_action(data, action)
    form = RoundForm(data, instance=rnd)

    if form.is_valid():
        rnd = form.save(commit=False)
        round_service.save_round(rnd, action)
        broadcast('round_updated', round_id=rnd.id, state=rnd.state)
        return success(_round(rnd))

    return error('validation_failed', 'Form validation failed.', 400, errors=form.errors.get_json_data())


@api_methods('POST')
@api_staff_required
def round_action(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    try:
        data = json.loads(request.body)
        action = data.get('action')
    except (ValueError, KeyError):
        return error('invalid_request', 'Action is required.', 400)

    try:
        round_service.apply_round_action(rnd, action)
    except round_service.UnknownRoundAction:
        return error('unknown_action', 'Unknown action.', 400)
    except round_service.RoundTransitionNotAllowed:
        return error('transition_not_allowed', 'That round cannot make this transition yet.', 403)

    broadcast('round_updated', round_id=rnd.id, state=rnd.state)
    return success({
        'round_id': rnd.pk,
        'action': action,
        'state': rnd.state,
    })


@api_methods('POST')
@api_staff_required
def round_archive(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    try:
        round_service.archive_round(rnd)
    except round_service.RoundNotRevealed:
        return error('round_not_revealed', 'Only completed rounds can be archived.', 403)
    broadcast('round_updated', round_id=rnd.id, state=rnd.state)
    return success({'round_id': rnd.pk, 'archived': True})


@api_methods('DELETE')
@api_staff_required
def round_delete(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    round_id = rnd.pk
    rnd.delete()
    broadcast('round_deleted', round_id=round_id)
    return success({'round_id': round_id, 'deleted': True})


@api_methods('GET')
@api_staff_required
def round_status(request, pk):
    value = get_object_or_404(Round.objects.select_related('season', 'host'), pk=pk)
    status = round_status_service.round_participation(value)
    return success({
        'round_id': value.pk, 'player_count': status.player_count,
        'submitted_count': status.submitted_count,
        'completed_count': status.completed_count,
        'players': [{
            'id': row['player'].pk,
            'username': row['player'].username,
            'display_name': row['player'].first_name or row['player'].username,
            'submitted': row['submission'] is not None,
            'submission': ({
                'id': row['submission'].pk,
                'title': row['submission'].title,
                'artist': row['submission'].artist,
            } if row['submission'] else None),
            'voted_count': row['voted_count'],
            'eligible_count': row['eligible_count'],
            'voting_started': row['voting_started'],
            'voting_complete': row['voting_complete'],
        } for row in status.rows],
    })

@api_methods('GET')
@api_staff_required
def players(request):
    query = request.GET.get('q', '').strip()
    return success({'query': query, 'players': [{
        'id': user.pk, 'username': user.username,
        'first_name': user.first_name, 'last_name': user.last_name,
        'email': user.email, 'is_active': user.is_active,
        'is_staff': user.is_staff, 'is_superuser': user.is_superuser,
        'approved': user.profile.approved, 'play_count': user.play_count,
    } for user in staff_service.players(query)]})


@api_methods('POST')
@api_staff_required
def user_action(request, pk):
    target = get_object_or_404(User, pk=pk)
    try:
        data = json.loads(request.body)
        action = data.get('action')
    except (ValueError, KeyError):
        return error('invalid_request', 'Action is required.', 400)

    try:
        result = membership_service.apply_membership_action(
            target, action, request.user,
        )
    except membership_service.SelfAccessChangeNotAllowed:
        return error('self_access_change_not_allowed', 'You cannot remove your own access.', 403)
    except membership_service.UnknownMembershipAction:
        return error('unknown_action', 'Unknown action.', 400)

    if action == 'approve' and result.approved_now:
        send_user_push(
            target, f'user:{target.pk}:approved', 'You’re approved!',
            'Your QueueUp account is ready. Tap to enter the league.', '/home/'
        )

    return success({
        'user_id': target.pk,
        'action': action,
        'approved': target.profile.approved,
        'is_active': target.is_active,
        'is_staff': target.is_staff,
    })


@api_methods('GET')
@api_staff_required
def badges(request):
    query = request.GET.get('q', '').strip()
    return success({'query': query, 'badges': [{
        'id': badge.pk, 'name': badge.name, 'slug': badge.slug,
        'description': badge.description, 'icon': badge.icon,
        'achievement_key': badge.achievement_key, 'hidden': badge.hidden,
        'active': badge.active, 'sort_order': badge.sort_order,
        'awarded_user_ids': [award.user_id for award in badge.awards.all()],
    } for badge in staff_service.badges(query)]})


@api_methods('POST', 'PATCH')
@api_staff_required
def badge_save(request, pk=None):
    badge = get_object_or_404(Badge, pk=pk) if pk else None
    try:
        data = json.loads(request.body)
    except ValueError:
        return error('invalid_json', 'Invalid JSON body.', 400)

    form = BadgeForm(data, instance=badge)
    if form.is_valid():
        badge = form.save()
        return success({
            'id': badge.pk, 'name': badge.name, 'slug': badge.slug,
        })

    return error('validation_failed', 'Form validation failed.', 400, errors=form.errors.get_json_data())


@api_methods('POST')
@api_staff_required
def badge_award(request, badge_pk, user_pk):
    badge = get_object_or_404(Badge, pk=badge_pk)
    target = get_object_or_404(User, pk=user_pk)
    result = badge_service.toggle_badge_award(target, badge, request.user)
    return success({
        'badge_id': badge.pk,
        'user_id': target.pk,
        'awarded': result.awarded,
    })


@api_methods('GET')
@api_staff_required
def seasons(request):
    return success({'seasons': [{
        'id': season.pk, 'name': season.name,
        'starts_at': season.starts_at.isoformat(),
        'ends_at': season.ends_at.isoformat(), 'active': season.active,
        'description': season.description, 'round_count': season.round_count,
    } for season in staff_service.seasons()]})


@api_methods('POST', 'PATCH')
@api_staff_required
def season_save(request, pk=None):
    season = get_object_or_404(Season, pk=pk) if pk else None
    # SeasonForm handles banner which is a file.
    # For now, we'll assume JSON for simple fields.
    # Multipart handling for banners might be needed if frontend sends it.
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
        except ValueError:
            return error('invalid_json', 'Invalid JSON body.', 400)
    else:
        data = request.POST

    form = SeasonForm(data, request.FILES or None, instance=season)
    if form.is_valid():
        season = form.save()
        return success({
            'id': season.pk, 'name': season.name,
        })

    return error('validation_failed', 'Form validation failed.', 400, errors=form.errors.get_json_data())
