from ..models import Submission
from ..spotify import api_get, extract_track_id, normalize_track


def search_tracks(query, round_obj=None):
    """Search Spotify and apply QueueUp's ISRC-only availability policy."""
    query = (query or '').strip()
    if len(query) < 2:
        return []
    pasted_id = extract_track_id(query)
    if pasted_id:
        tracks = [normalize_track(api_get(f'/tracks/{pasted_id}'))]
    else:
        result = api_get('/search', {
            'q': query, 'type': 'track', 'limit': 10,
        })
        tracks = [normalize_track(item) for item in result['tracks']['items']]
    used_isrcs = set()
    if round_obj:
        used_isrcs = {
            value for value in Submission.objects.filter(
                round=round_obj,
            ).values_list('isrc', flat=True) if value
        }
    for track in tracks:
        track['used'] = bool(
            track.get('isrc') and track['isrc'] in used_isrcs
        )
        track['available'] = not track['used']
    return tracks
