from django.db import IntegrityError, transaction

from ..models import Submission, UserProfile
from ..spotify import api_get, extract_track_id, genres_for_artists, normalize_track


class SubmissionClosed(Exception):
    pass


class InvalidTrackReference(Exception):
    pass


class ExplicitTrack(Exception):
    pass


class SubmissionRulesNotAccepted(Exception):
    pass


class DuplicateRecording(Exception):
    pass


class SpotifyVerificationFailed(Exception):
    pass


class SubmissionConflict(Exception):
    pass


def create_submission(round_obj, user, track_reference):
    """Verify a Spotify track and create an eligible round submission."""
    if round_obj.state != 'submitting':
        raise SubmissionClosed

    track_id = extract_track_id(track_reference)
    if not track_id:
        raise InvalidTrackReference

    try:
        track = normalize_track(api_get(f'/tracks/{track_id}'))
        if track.get('explicit'):
            raise ExplicitTrack

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.submission_rules_accepted_at:
            raise SubmissionRulesNotAccepted

        genres = genres_for_artists(track['artist_ids'])
        if (
            track.get('isrc')
            and Submission.objects.filter(
                round=round_obj,
                isrc=track['isrc'],
            ).exists()
        ):
            raise DuplicateRecording
    except (ExplicitTrack, SubmissionRulesNotAccepted, DuplicateRecording):
        raise
    except Exception as exc:
        raise SpotifyVerificationFailed from exc

    try:
        with transaction.atomic():
            return Submission.objects.create(
                round=round_obj,
                user=user,
                spotify_track_id=track['id'],
                isrc=track.get('isrc') or None,
                spotify_uri=track['uri'],
                spotify_url=track['url'],
                title=track['title'],
                artist=track['artist'],
                artist_ids=track['artist_ids'],
                genres=genres,
                album=track['album'],
                album_art_url=track['art'],
                preview_url=track['preview'],
                explicit=False,
            )
    except IntegrityError as exc:
        raise SubmissionConflict from exc
