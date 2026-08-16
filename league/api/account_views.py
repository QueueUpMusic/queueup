import json
from urllib.parse import unquote

from django.conf import settings

from ..forms import ProfileForm, ProfilePictureForm
from ..services import media as media_service
from ..services import membership as membership_service
from ..services import notifications as notification_service
from .auth import api_methods, api_user_required
from .responses import error, success


def _json_body(request):
    try:
        value = json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _picture_url(user):
    return user.profile.picture.url if user.profile.picture else None


@api_methods('POST')
@api_user_required
def update_profile(request):
    body = _json_body(request)
    if body is None:
        return error('invalid_json', 'A JSON object is required.', 400)
    form = ProfileForm(body, user=request.user)
    if not form.is_valid():
        message = next(iter(form.errors.values()))[0]
        return error('invalid_profile', str(message), 400)
    form.save()
    return success({
        'display_name': request.user.first_name or request.user.username,
        'picture_url': _picture_url(request.user),
    })


@api_methods('GET')
@api_user_required
def onboarding_state(request):
    season = membership_service.current_season()
    profile = request.user.profile
    return success({
        'season_welcome': {
            'season_id': season.pk,
            'name': season.name,
            'acknowledged': request.user.season_welcomes.filter(
                season=season,
            ).exists(),
        } if season else None,
        'voting_guide_seen': profile.voting_guide_seen,
        'submission_rules_accepted': bool(
            profile.submission_rules_accepted_at
        ),
    })


@api_methods('POST')
@api_user_required
def acknowledge_season(request):
    welcome, created = membership_service.acknowledge_current_season(
        request.user,
    )
    if welcome is None:
        return success({'season_welcome': None, 'created': False})
    return success({
        'season_welcome': {
            'season_id': welcome.season_id,
            'acknowledged': True,
        },
        'created': created,
    })


@api_methods('POST')
@api_user_required
def acknowledge_voting_guide(request):
    profile = membership_service.mark_voting_guide_seen(request.user)
    return success({'voting_guide_seen': profile.voting_guide_seen})


@api_methods('POST')
@api_user_required
def accept_submission_rules(request):
    profile = membership_service.accept_submission_rules(request.user)
    return success({
        'submission_rules_accepted': True,
        'accepted_at': profile.submission_rules_accepted_at.isoformat(),
    })


def _picture_form(request):
    raw_upload = request.headers.get('X-QueueUp-Raw-Upload') == '1'
    if raw_upload:
        try:
            content_length = int(request.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            content_length = 0
        if content_length > media_service.PROFILE_PICTURE_MAX_BYTES:
            return None, 'Please choose an image smaller than 5 MB.'
        picture_bytes = request.read(
            media_service.PROFILE_PICTURE_MAX_BYTES + 1,
        )
        if not picture_bytes:
            return None, 'The selected image was empty. Please choose it again.'
        if len(picture_bytes) > media_service.PROFILE_PICTURE_MAX_BYTES:
            return None, 'Please choose an image smaller than 5 MB.'
        encoded_name = request.headers.get(
            'X-QueueUp-Filename', 'profile-picture',
        )
        filename = (
            unquote(encoded_name).replace('\\', '/').rsplit('/', 1)[-1].strip()
            or 'profile-picture'
        )
        picture = media_service.uploaded_profile_picture(
            filename,
            request.content_type or 'application/octet-stream',
            picture_bytes,
            add_content_type_extension=True,
        )
        return ProfilePictureForm(files={'picture': picture}), None

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
    return ProfilePictureForm(request.POST, files), None


@api_methods('POST', 'DELETE')
@api_user_required
def profile_picture(request):
    if request.method == 'DELETE':
        removed = media_service.remove_profile_picture(request.user)
        return success({'removed': removed, 'picture_url': None})
    form, upload_error = _picture_form(request)
    if upload_error:
        return error('invalid_picture', upload_error, 400)
    if not form.is_valid():
        message = next(iter(form.errors.values()))[0]
        return error('invalid_picture', str(message), 400)
    profile = media_service.replace_profile_picture(
        request.user, form.cleaned_data['picture'],
    )
    return success({'picture_url': profile.picture.url})


@api_methods('GET')
@api_user_required
def notification_preferences(request):
    subscriptions = request.user.push_subscriptions.order_by('created_at')
    return success({
        'webpush_public_key': settings.WEBPUSH_PUBLIC_KEY,
        'push_enabled': subscriptions.exists(),
        'subscriptions': [
            {'id': row.pk, 'endpoint': row.endpoint}
            for row in subscriptions
        ],
    })


@api_methods('POST', 'DELETE')
@api_user_required
def push_subscriptions(request):
    body = _json_body(request)
    if body is None:
        return error('invalid_json', 'A JSON object is required.', 400)
    if request.method == 'DELETE':
        removed = notification_service.remove_push_subscription(
            request.user, body.get('endpoint'),
        )
        return success({'removed': removed})
    try:
        subscription = notification_service.register_push_subscription(
            request.user, body,
        )
    except notification_service.InvalidPushSubscription:
        return error('invalid_subscription', 'Invalid push subscription.', 400)
    return success({
        'subscription': {
            'id': subscription.pk,
            'endpoint': subscription.endpoint,
        },
    }, status=201)
