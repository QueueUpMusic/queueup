import json
import io
import secrets
import base64
import qrcode
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Count
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from urllib.parse import unquote
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.cache import never_cache

from .forms import BadgeForm, ProfileForm, ProfilePictureForm, RoundForm, SeasonForm, SignupForm, VoteForm
from .services.achievements import earned_badges, prestige_badges
from .realtime import broadcast
from .models import Badge, PushSubscription, Round, Season, SeasonWelcome, SpotifyConnection, Submission, UserProfile, Vote
from .spotify import exchange_code, spotify_authorize_url, user_api
from .services.rounds import homepage_rounds, revealed_rounds_for_archive, round_detail_for_user
from .services.scoring import SUBMISSION_BONUS_POINTS, season_leaderboard
from .services.spotify_search import search_tracks
from .services.profiles import profile_for_user
from .services import rounds as round_service
from .services import round_status as round_status_service
from .services import badges as badge_service
from .services import membership as membership_service
from .services import media as media_service
from .services import submissions as submission_service
from .services import votes as vote_service
from .push import send_user_push
from .voting import voting_progress


def health(request):
    return JsonResponse({'status': 'ok'})


def landing(request):
    return redirect('home') if request.user.is_authenticated else render(request, 'league/landing.html')


def signup(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        profile = membership_service.initialize_user_membership(user)
        login(request, user)
        return redirect('home' if profile.approved else 'waiting_approval')
    return render(request, 'league/signup.html', {'form': form})


@login_required
def waiting_approval(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if request.user.is_staff or request.user.is_superuser or profile.approved:
        return redirect('home')
    return render(request, 'league/waiting_approval.html', {'vapid_public_key': settings.WEBPUSH_PUBLIC_KEY})


def terms(request):
    return render(request, 'league/terms.html')


def privacy(request):
    return render(request, 'league/privacy.html')


@login_required
def home(request):
    current, results_round = homepage_rounds()

    submission = Submission.objects.filter(round=current, user=request.user).first() if current else None
    return render(request, 'league/home.html', {
        'round': current,
        'results_round': results_round,
        'submission': submission,
        'current_first': bool(current and current.state != 'upcoming'),
    })


@login_required
def round_detail(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    if not rnd.is_visible and not request.user.is_staff:
        return HttpResponse(status=404)
    detail = round_detail_for_user(rnd, request.user)
    return render(request, 'league/round.html', {
        'round': rnd, 'mine': detail.mine, 'submissions': detail.submissions,
        'ranked_results': detail.ranked_results, 'ranking_by_id': detail.ranking_by_id,
        'winners': detail.winners, 'voted_ids': detail.ballot.voted_ids,
        'vote_scores': detail.ballot.vote_scores,
        'voted_count': detail.ballot.voted_count,
        'eligible_count': detail.ballot.eligible_count,
        'voting_complete': detail.ballot.complete,
        'no_votable_songs': detail.ballot.no_votable_songs,
        'vote_form': VoteForm(), 'show_voting_guide': detail.show_voting_guide,
    })


@login_required
def song_picker(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    if not rnd.is_visible and not request.user.is_staff:
        return HttpResponse(status=404)
    if rnd.state != 'submitting':
        messages.error(request, 'Submissions are closed.')
        return redirect('round_detail', pk=pk)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    return render(request, 'league/song_picker.html', {'round': rnd, 'show_submission_rules': not profile.submission_rules_accepted_at})


@login_required
def spotify_search(request):
    q = request.GET.get('q', '').strip()
    round_id = request.GET.get('round')
    search_round = None
    if round_id:
        search_round = Round.objects.filter(pk=round_id).first()
        if search_round and not (search_round.is_visible or request.user.is_staff):
            search_round = None
    try:
        return JsonResponse({'tracks': search_tracks(q, search_round)})
    except Exception as exc:
        return JsonResponse({'tracks': [], 'error': str(exc)}, status=502)


@login_required
@require_POST
def submit_song(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    try:
        submission_service.create_submission(
            rnd,
            request.user,
            request.POST.get('track_id', ''),
        )
    except submission_service.SubmissionClosed:
        messages.error(request, 'Submissions are closed.')
        return redirect('round_detail', pk=pk)
    except submission_service.InvalidTrackReference:
        messages.error(request, 'That does not look like a valid Spotify track.')
        return redirect('song_picker', pk=pk)
    except submission_service.ExplicitTrack:
        messages.error(request, 'Keep it clean please! No explicit songs allowed.')
        return redirect('song_picker', pk=pk)
    except submission_service.SubmissionRulesNotAccepted:
        messages.error(request, 'Please confirm the clean, all-ages music rule before submitting.')
        return redirect('song_picker', pk=pk)
    except submission_service.DuplicateRecording:
        messages.error(request, 'That recording has already been submitted for this round.')
        return redirect('song_picker', pk=pk)
    except submission_service.SpotifyVerificationFailed:
        messages.error(request, 'That Spotify track could not be verified.')
        return redirect('song_picker', pk=pk)
    except submission_service.SubmissionConflict:
        messages.error(request, 'You already submitted, or somebody chose that song first.')
        return redirect('round_detail', pk=pk)

    messages.success(request, f'Your song is locked in. You earned {SUBMISSION_BONUS_POINTS} points for submitting!')
    broadcast('submission_added', round_id=rnd.id, submissions=rnd.submissions.count())
    return redirect('round_detail', pk=pk)


@login_required
@require_POST
def vote(request, pk, submission_id):
    rnd = get_object_or_404(Round, pk=pk)
    sub = get_object_or_404(Submission, pk=submission_id, round=rnd)
    try:
        command = vote_service.prepare_vote(rnd, request.user, sub)
    except vote_service.VotingClosed:
        messages.error(request, 'Voting is not open.')
        return redirect('round_detail', pk=pk)
    except vote_service.SelfVoteNotAllowed:
        messages.error(request, "You can't vote for your own song.")
        return redirect('round_detail', pk=pk)
    form = VoteForm(request.POST)
    if form.is_valid():
        result = command.save(form.cleaned_data['score'])
        progress = result.progress
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'ok': True, 'score': form.cleaned_data['score'],
                'complete': progress['complete'], 'voted_count': progress['voted_count'],
                'eligible_count': progress['eligible_count'],
                'complete_url': reverse('voting_complete', args=[rnd.pk]),
            })
        messages.success(request, 'Vote saved.')
        broadcast('vote_saved', round_id=rnd.id, votes=rnd.votes.count())
    return redirect('round_detail', pk=pk)


@login_required
def voting_complete(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    if not rnd.is_visible and not request.user.is_staff:
        return HttpResponse(status=404)
    progress = voting_progress(rnd, request.user)
    if rnd.state == 'voting' and not progress['complete'] and not progress['no_votable_songs']:
        return redirect('round_detail', pk=pk)
    return render(request, 'league/voting_complete.html', {'round': rnd, **progress})


@login_required
def leaderboard(request):
    seasons = Season.objects.order_by('-starts_at', '-id')
    selected_season = None
    season_id = request.GET.get('season')
    if season_id:
        selected_season = seasons.filter(pk=season_id).first()
    if selected_season is None:
        selected_season = seasons.filter(active=True).first() or seasons.first()

    players = User.objects.none()
    if selected_season:
        players = season_leaderboard(selected_season)

    return render(request, 'league/leaderboard.html', {
        'players': players,
        'seasons': seasons,
        'selected_season': selected_season,
    })


@login_required
def stats(request, username=None):
    profile_user = get_object_or_404(User, username=username) if username else request.user
    season_id = request.GET.get('season')
    profile = profile_for_user(profile_user, season_id=season_id)
    return render(request, 'league/stats.html', {
        'profile_user': profile.profile_user,
        'submissions': profile.submissions,
        'best': profile.best,
        'wins': profile.wins,
        'podiums': profile.podiums,
        'round_count': profile.round_count,
        'genres': profile.genres,
        'artists': profile.artists,
        'avg_received': profile.avg_received,
        'avg_placement': profile.avg_placement,
        'win_rate': profile.win_rate,
        'badges': earned_badges(profile_user),
        'prestige_badges': prestige_badges(profile_user),
        'seasons': profile.seasons,
        'selected_season': profile.selected_season,
    })


@login_required
@never_cache
@ensure_csrf_cookie
def profile_edit(request):
    UserProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Your profile has been updated.')
        return redirect('stats')
    return render(request, 'league/profile_edit.html', {'form': form})


def _picture_upload_error(request, message, raw_upload=False):
    if raw_upload:
        return JsonResponse({'ok': False, 'error': str(message)}, status=400)
    messages.error(request, message)
    return redirect('profile_edit')


@csrf_exempt
@login_required
@require_POST
def upload_profile_picture(request):
    raw_upload = request.headers.get('X-QueueUp-Raw-Upload') == '1'

    if raw_upload:
        try:
            content_length = int(request.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            content_length = 0
        if content_length > media_service.PROFILE_PICTURE_MAX_BYTES:
            return _picture_upload_error(request, 'Please choose an image smaller than 5 MB.', True)

        picture_bytes = request.read(media_service.PROFILE_PICTURE_MAX_BYTES + 1)
        if not picture_bytes:
            return _picture_upload_error(request, 'The selected image was empty. Please choose it again.', True)
        if len(picture_bytes) > media_service.PROFILE_PICTURE_MAX_BYTES:
            return _picture_upload_error(request, 'Please choose an image smaller than 5 MB.', True)

        encoded_name = request.headers.get('X-QueueUp-Filename', 'profile-picture')
        filename = unquote(encoded_name).replace('\\', '/').rsplit('/', 1)[-1].strip() or 'profile-picture'
        content_type = request.content_type or 'application/octet-stream'
        picture = media_service.uploaded_profile_picture(
            filename,
            content_type,
            picture_bytes,
            add_content_type_extension=True,
        )
        form = ProfilePictureForm(files={'picture': picture})
    else:
        picture = request.FILES.get('picture')
        if picture is None and request.FILES:
            picture = next(iter(request.FILES.values()))
        if picture is not None and picture.size <= media_service.PROFILE_PICTURE_MAX_BYTES:
            picture_bytes = picture.read(media_service.PROFILE_PICTURE_MAX_BYTES + 1)
            picture = media_service.uploaded_profile_picture(
                picture.name,
                picture.content_type or 'application/octet-stream',
                picture_bytes,
            )
        files = {'picture': picture} if picture is not None else {}
        form = ProfilePictureForm(request.POST, files)

    if not form.is_valid():
        error = next(iter(form.errors.values()))[0]
        return _picture_upload_error(request, error, raw_upload)

    media_service.replace_profile_picture(
        request.user,
        form.cleaned_data['picture'],
    )
    messages.success(request, 'Your profile picture has been updated.')
    if raw_upload:
        return JsonResponse({'ok': True, 'redirect': reverse('profile_edit')})
    return redirect('profile_edit')


@login_required
@require_POST
def remove_profile_picture(request):
    if media_service.remove_profile_picture(request.user):
        messages.success(request, 'Your profile picture has been removed.')
    return redirect('profile_edit')


@login_required
def archive(request):
    return render(request, 'league/archive.html', {
        'rounds': revealed_rounds_for_archive(),
    })


@login_required
@require_POST
def push_subscribe(request):
    try:
        data = json.loads(request.body)
        keys = data['keys']
        PushSubscription.objects.update_or_create(
            endpoint=data['endpoint'], defaults={'user': request.user, 'p256dh': keys['p256dh'], 'auth': keys['auth']}
        )
        return JsonResponse({'ok': True})
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest('Invalid subscription')


@login_required
@require_POST
def push_unsubscribe(request):
    try:
        endpoint = json.loads(request.body).get('endpoint')
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
        return JsonResponse({'ok': True})
    except ValueError:
        return HttpResponseBadRequest('Invalid request')


@login_required
def notification_settings(request):
    return render(request, 'league/notifications.html', {'vapid_public_key': settings.WEBPUSH_PUBLIC_KEY})


staff_required = user_passes_test(lambda u: u.is_staff)


@staff_required
def control_panel(request):
    signup_url = f"{settings.PUBLIC_URL}{reverse('signup')}"
    qr = qrcode.make(signup_url)
    out = io.BytesIO()
    qr.save(out, format='PNG')
    qr_data = base64.b64encode(out.getvalue()).decode()
    spotify_connection = SpotifyConnection.objects.filter(user=request.user).first()
    return render(request, 'league/control_panel.html', {
        'round_count': Round.objects.count(),
        'badge_count': Badge.objects.count(),
        'season_count': Season.objects.count(),
        'user_count': User.objects.count(),
        'signup_url': signup_url,
        'signup_qr': qr_data,
        'spotify_connection': spotify_connection,
    })


@staff_required
def control_rounds(request):
    query = request.GET.get('q', '').strip()
    rounds = round_status_service.control_rounds_with_status(query)

    return render(request, 'league/control_rounds.html', {
        'rounds': rounds,
        'query': query,
        'spotify_connection': SpotifyConnection.objects.filter(user=request.user).first(),
    })


@staff_required
def control_badges(request):
    query = request.GET.get('q', '').strip()
    badges = Badge.objects.order_by('sort_order', 'name')
    if query:
        badges = badges.filter(
            models.Q(name__icontains=query)
            | models.Q(description__icontains=query)
            | models.Q(achievement_key__icontains=query)
        )
    users = User.objects.select_related('profile').order_by('first_name', 'username')
    return render(request, 'league/control_badges.html', {
        'badges': badges,
        'users': users,
        'query': query,
    })


@staff_required
def control_users(request):
    query = request.GET.get('q', '').strip()
    users = User.objects.select_related('profile').annotate(
        play_count=Count('submissions', distinct=True)
    ).order_by('-date_joined')
    if query:
        users = users.filter(
            models.Q(username__icontains=query)
            | models.Q(first_name__icontains=query)
            | models.Q(last_name__icontains=query)
            | models.Q(email__icontains=query)
        )
    return render(request, 'league/control_users.html', {
        'users': users,
        'query': query,
    })


@staff_required
def round_status(request, pk):
    rnd = get_object_or_404(Round.objects.select_related('season', 'host'), pk=pk)
    participation = round_status_service.round_participation(rnd)

    return render(request, 'league/round_status.html', {
        'round': rnd,
        'rows': participation.rows,
        'player_count': participation.player_count,
        'submitted_count': participation.submitted_count,
        'completed_count': participation.completed_count,
    })


@staff_required
def season_create(request):
    form = SeasonForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Season created.')
        return redirect('control_panel')
    return render(request, 'league/admin_form.html', {'form': form, 'heading': 'Create season', 'multipart': True})


@staff_required
def round_create(request):
    data = request.POST.copy() if request.method == 'POST' else None
    action = request.POST.get('save_action') if request.method == 'POST' else None
    data = round_service.round_form_data_for_action(data, action)
    form = RoundForm(data)
    if request.method == 'POST' and form.is_valid():
        rnd = form.save(commit=False)
        round_service.save_round(rnd, action)
        messages.success(request, 'Round saved as a draft.' if rnd.is_draft else ('Round published.' if action == 'publish' else 'Round created.'))
        return redirect('control_rounds')
    return render(request, 'league/admin_form.html', {'form': form, 'heading': 'Create round', 'back_url': reverse('control_rounds'), 'round_actions': True})


@staff_required
@require_POST
def round_action(request, pk, action):
    rnd = get_object_or_404(Round, pk=pk)
    try:
        round_service.apply_round_action(rnd, action)
    except round_service.UnknownRoundAction:
        return HttpResponseBadRequest('Unknown action')
    except round_service.RoundTransitionNotAllowed:
        messages.error(request, 'That round cannot make this transition yet.')
        return redirect('control_rounds')
    messages.success(request, f'Round updated: {action.replace("_", " ")}.')
    broadcast('round_updated', round_id=rnd.id, state=rnd.state)
    return redirect('control_rounds')



def service_worker(request):
    content = """const CACHE='queueup-v7.0.1';
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(Promise.all([caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key)))),self.clients.claim()])));
self.addEventListener('fetch',event=>{if(event.request.mode==='navigate')event.respondWith(fetch(event.request));});
self.addEventListener('push',event=>{let data={title:'QueueUp',body:'There is an update.',url:'/home/'};try{data=event.data.json()}catch(_){}const url=new URL(data.url||'/home/',self.location.origin);if(url.origin!==self.location.origin)url.pathname='/home/';event.waitUntil(self.registration.showNotification(data.title,{body:data.body,icon:'/static/league/icon-192-v5.5.4.png',badge:'/static/league/icon-192-v5.5.4.png',tag:data.tag||undefined,data:{url:url.pathname+url.search}}))});
self.addEventListener('notificationclick',event=>{event.notification.close();const target=new URL(event.notification.data.url||'/home/',self.location.origin).href;event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(windows=>{for(const windowClient of windows){if(new URL(windowClient.url).origin===self.location.origin){if('navigate'in windowClient)windowClient.navigate(target);return windowClient.focus()}}return clients.openWindow(target)}))});"""
    response = HttpResponse(content, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@staff_required
def season_edit(request, pk):
    season = get_object_or_404(Season, pk=pk); form = SeasonForm(request.POST or None, request.FILES or None, instance=season)
    if request.method == 'POST' and form.is_valid(): form.save(); messages.success(request, 'Season updated.'); return redirect('control_panel')
    return render(request, 'league/admin_form.html', {'form': form, 'heading': 'Edit season', 'multipart': True})

@staff_required
def round_edit(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    data = request.POST.copy() if request.method == 'POST' else None
    action = request.POST.get('save_action') if request.method == 'POST' else None
    data = round_service.round_form_data_for_action(data, action)
    form = RoundForm(data, instance=rnd)
    if request.method == 'POST' and form.is_valid():
        rnd = form.save(commit=False)
        round_service.save_round(rnd, action)
        broadcast('round_updated', round_id=rnd.id, state=rnd.state)
        messages.success(request, 'Round saved as a draft.' if rnd.is_draft else ('Round published.' if action == 'publish' else 'Round updated.'))
        return redirect('control_rounds')
    return render(request, 'league/admin_form.html', {'form': form, 'heading': 'Edit round', 'back_url': reverse('control_rounds'), 'round_actions': True})

@staff_required
@require_POST
def round_archive(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    try:
        round_service.archive_round(rnd)
    except round_service.RoundNotRevealed:
        messages.error(request, 'Only completed rounds can be archived.')
        return redirect('control_rounds')
    messages.success(request, f'Archived “{rnd.prompt}”. It remains available on the Archive page.')
    broadcast('round_updated', round_id=rnd.id, state=rnd.state)
    return redirect('control_rounds')


@staff_required
@require_POST
def round_delete(request, pk):
    rnd = get_object_or_404(Round, pk=pk); prompt=rnd.prompt; rnd.delete(); messages.success(request, f'Deleted “{prompt}”.'); broadcast('round_deleted', round_id=pk); return redirect('control_rounds')

@staff_required
@require_POST
def user_action(request, pk, action):
    target = get_object_or_404(User, pk=pk)
    try:
        result = membership_service.apply_membership_action(
            target, action, request.user,
        )
    except membership_service.SelfAccessChangeNotAllowed:
        messages.error(request, 'You cannot remove your own access.')
        return redirect('control_users')
    except membership_service.UnknownMembershipAction:
        return HttpResponseBadRequest('Unknown action')
    if action == 'approve':
        if result.approved_now:
            send_user_push(target, f'user:{target.pk}:approved', 'You’re approved!', 'Your QueueUp account is ready. Tap to enter the league.', '/home/')
        messages.success(request, f'Approved {target.username}.')
        return redirect('control_users')
    messages.success(request, f'Updated {target.username}.')
    return redirect('control_users')

@staff_required
def spotify_connect(request):
    state = secrets.token_urlsafe(24); request.session['spotify_oauth_state'] = state
    return redirect(spotify_authorize_url(state))

@staff_required
def spotify_callback(request):
    if request.GET.get('state') != request.session.pop('spotify_oauth_state', None):
        messages.error(request, 'Spotify authorization state did not match.'); return redirect('control_panel')
    try:
        data = exchange_code(request.GET.get('code',''))
        expires = timezone.now() + timedelta(seconds=data['expires_in'])
        temp = type('Connection', (), {'access_token': data['access_token'], 'refresh_token': data.get('refresh_token',''), 'expires_at': expires, 'expired': False})()
        me = user_api(temp, 'GET', '/me')
        SpotifyConnection.objects.update_or_create(user=request.user, defaults={'spotify_user_id': me['id'], 'display_name': me.get('display_name',''), 'access_token': data['access_token'], 'refresh_token': data.get('refresh_token',''), 'expires_at': expires, 'scope': data.get('scope','')})
        messages.success(request, 'Spotify connected for playlist creation.')
    except Exception as exc: messages.error(request, f'Spotify connection failed: {exc}')
    return redirect('control_panel')

@staff_required
@require_POST
def create_playlist(request, pk):
    rnd = get_object_or_404(Round, pk=pk)
    connection = SpotifyConnection.objects.filter(user=request.user).first()
    if not connection: messages.error(request, 'Connect your Spotify account first.'); return redirect('control_panel')
    try:
        playlist = user_api(connection, 'POST', '/me/playlists', {'name': f'{rnd.season.name} · {rnd.prompt}'[:100], 'description': 'Created by QueueUp', 'public': False})
        uris = list(rnd.submissions.order_by('submitted_at').values_list('spotify_uri', flat=True))
        if uris: user_api(connection, 'POST', f"/playlists/{playlist['id']}/items", {'uris': uris})
        rnd.playlist_url = playlist['external_urls']['spotify']; rnd.save(update_fields=['playlist_url'])
        messages.success(request, 'Spotify playlist created.')
    except Exception as exc: messages.error(request, f'Playlist creation failed: {exc}')
    return redirect('control_panel')

@login_required
def live_status(request):
    now = timezone.now()
    rnd = Round.objects.filter(models.Q(goes_live_at__isnull=True) | models.Q(goes_live_at__lte=now), is_draft=False).first()
    if not rnd: return JsonResponse({'round': None})
    return JsonResponse({'round': {'id': rnd.id, 'state': rnd.state, 'submissions': rnd.submissions.count(), 'votes': rnd.votes.count()}})


@login_required
@require_POST
def season_welcome_seen(request, pk):
    season = get_object_or_404(Season, pk=pk)
    SeasonWelcome.objects.get_or_create(user=request.user, season=season)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def voting_guide_seen(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not profile.voting_guide_seen:
        profile.voting_guide_seen = True
        profile.save(update_fields=['voting_guide_seen'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def accept_submission_rules(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile.submission_rules_accepted_at = timezone.now()
    profile.save(update_fields=['submission_rules_accepted_at'])
    return JsonResponse({'ok': True})


@staff_required
def badge_create(request):
    form = BadgeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Badge created.')
        return redirect('control_badges')
    return render(request, 'league/admin_form.html', {'form': form, 'heading': 'Create badge', 'back_url': reverse('control_badges')})


@staff_required
def badge_edit(request, pk):
    badge = get_object_or_404(Badge, pk=pk)
    form = BadgeForm(request.POST or None, instance=badge)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Badge updated.')
        return redirect('control_badges')
    return render(request, 'league/admin_form.html', {'form': form, 'heading': 'Edit badge', 'back_url': reverse('control_badges')})


@staff_required
@require_POST
def badge_award(request, badge_pk, user_pk):
    badge = get_object_or_404(Badge, pk=badge_pk)
    target = get_object_or_404(User, pk=user_pk)
    result = badge_service.toggle_badge_award(target, badge, request.user)
    if not result.awarded:
        messages.success(request, f'Removed {badge.name} from {target.username}.')
    else:
        messages.success(request, f'Awarded {badge.name} to {target.username}.')
    return redirect('control_badges')
