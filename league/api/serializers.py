from decimal import Decimal

from django.utils import timezone

from ..models import Submission
from ..ranking import RankedItem


def iso(value):
    return value.isoformat() if value else None


def user_summary(user):
    return {
        'id': user.pk,
        'username': user.username,
        'display_name': user.first_name or user.username,
        'picture_url': user.profile.picture.url if user.profile.picture else None,
    }


def host_summary(user):
    if not user:
        return None
    return {
        'display_name': user.first_name or user.username,
        'picture_url': user.profile.picture.url if user.profile.picture else None,
    }


def session_user(user):
    approved = user.is_staff or user.is_superuser or user.profile.approved
    return {
        'id': user.pk,
        'username': user.username,
        'display_name': user.first_name or user.username,
        'email': user.email,
        'approved': approved,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
    }


def season_summary(season):
    return {
        'id': season.pk,
        'name': season.name,
        'description': season.description,
        'starts_at': iso(season.starts_at),
        'ends_at': iso(season.ends_at),
        'active': season.active,
        'banner_url': season.banner.url if season.banner else None,
    }


def round_summary(round_obj):
    submission_count = getattr(round_obj, 'submission_count', None)
    if submission_count is None:
        submission_count = round_obj.submissions.count()
    rating_count = getattr(round_obj, 'rating_count', None)
    if rating_count is None:
        rating_count = round_obj.votes.count()
    data = {
        'id': round_obj.pk,
        'season': season_summary(round_obj.season),
        'prompt': round_obj.prompt,
        'details': round_obj.details,
        'state': round_obj.state,
        'submission_opens': iso(round_obj.submission_opens),
        'submission_deadline': iso(round_obj.submission_deadline),
        'voting_deadline': iso(round_obj.voting_deadline),
        'reveal_at': iso(round_obj.reveal_at),
        'archived': round_obj.archived,
        'submission_count': submission_count,
        'rating_count': rating_count,
        'host': host_summary(round_obj.host),
    }
    if round_obj.state == 'revealed':
        data['playlist_url'] = round_obj.playlist_url or None
    return data


def submission_track(submission):
    return {
        'id': submission.pk,
        'spotify_track_id': submission.spotify_track_id,
        'spotify_uri': submission.spotify_uri,
        'spotify_url': submission.spotify_url,
        'title': submission.title,
        'artist': submission.artist,
        'album': submission.album,
        'album_art_url': submission.album_art_url,
        'preview_url': submission.preview_url,
    }


def revealed_submission(submission, ranking=None):
    data = submission_track(submission)
    data['submitter'] = user_summary(submission.user)
    data['average_score'] = float(submission.avg or 0)
    data['vote_count'] = submission.vote_count
    if ranking:
        data.update({
            'place': ranking.place,
            'tied': ranking.tied,
            'place_label': ranking.label,
        })
    return data


def badge_summary(row):
    hidden = row['hidden'] and not row['earned']
    return {
        'key': row['key'],
        'name': row['name'],
        'description': 'Hidden achievement' if hidden else row['description'],
        'icon': row['icon'],
        'earned': row['earned'],
        'hidden': row['hidden'],
    }


def _recap_value(value):
    """Convert the existing recap read model into JSON-safe native data."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Submission):
        return submission_track(value)
    if isinstance(value, RankedItem):
        return {
            'player': user_summary(value.item),
            'score': float(value.score),
            'place': value.place,
            'tied': value.tied,
            'place_label': value.label,
        }
    if isinstance(value, dict):
        return {key: _recap_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_recap_value(item) for item in value]
    return value


def recap_payload(recap):
    """Serialize SeasonRecap without changing its authoritative calculations."""
    return {
        'season': season_summary(recap.season),
        'slides': _recap_value(recap.slides),
        'summary': _recap_value(recap.summary),
    }


def countdown_summary(countdown, now=None):
    now = now or timezone.now()
    return {
        'id': countdown.pk,
        'title': countdown.title,
        'target_at': iso(countdown.target_at),
        'state': 'expired' if countdown.target_at <= now else 'counting_down',
    }
