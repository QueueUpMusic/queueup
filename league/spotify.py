import base64
import os
import re
import time

import requests

_TOKEN = {'value': None, 'expires': 0}


def get_token():
    if _TOKEN['value'] and _TOKEN['expires'] > time.time() + 30:
        return _TOKEN['value']
    client_id = os.getenv('SPOTIFY_CLIENT_ID', '')
    secret = os.getenv('SPOTIFY_CLIENT_SECRET', '')
    if not client_id or not secret:
        raise RuntimeError('Spotify credentials are missing from .env.')
    basic = base64.b64encode(f'{client_id}:{secret}'.encode()).decode()
    response = requests.post('https://accounts.spotify.com/api/token', headers={'Authorization': f'Basic {basic}'}, data={'grant_type': 'client_credentials'}, timeout=15)
    response.raise_for_status()
    data = response.json()
    _TOKEN.update(value=data['access_token'], expires=time.time() + data['expires_in'])
    return _TOKEN['value']


def api_get(path, params=None):
    response = requests.get(f'https://api.spotify.com/v1{path}', headers={'Authorization': f'Bearer {get_token()}'}, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def extract_track_id(value):
    value = (value or '').strip()
    match = re.search(r'(?:track/|spotify:track:)([A-Za-z0-9]{22})', value)
    if match:
        return match.group(1)
    if re.fullmatch(r'[A-Za-z0-9]{22}', value):
        return value
    return None


def normalize_track(track):
    images = track.get('album', {}).get('images', [])
    artists = track.get('artists', [])
    return {
        'id': track['id'],
        'uri': track['uri'],
        'url': track['external_urls']['spotify'],
        'title': track['name'],
        'artist': ', '.join(a['name'] for a in artists),
        'artist_ids': [a['id'] for a in artists if a.get('id')],
        'album': track.get('album', {}).get('name', ''),
        'art': images[0]['url'] if images else '',
        'preview': track.get('preview_url') or '',
        'explicit': bool(track.get('explicit', False)),
        'isrc': (track.get('external_ids', {}).get('isrc') or '').strip().upper(),
    }


def genres_for_artists(artist_ids):
    genres = []
    for artist_id in artist_ids[:5]:
        try:
            artist = api_get(f'/artists/{artist_id}')
            genres.extend(artist.get('genres', []))
        except Exception:
            continue
    return sorted(set(genres))[:20]

from urllib.parse import urlencode
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

AUTH_SCOPES = 'playlist-modify-private playlist-modify-public'

def spotify_authorize_url(state):
    return 'https://accounts.spotify.com/authorize?' + urlencode({
        'client_id': os.getenv('SPOTIFY_CLIENT_ID', ''), 'response_type': 'code',
        'redirect_uri': settings.SPOTIFY_REDIRECT_URI, 'scope': AUTH_SCOPES,
        'state': state, 'show_dialog': 'true',
    })

def exchange_code(code):
    response = requests.post('https://accounts.spotify.com/api/token', data={
        'grant_type': 'authorization_code', 'code': code, 'redirect_uri': settings.SPOTIFY_REDIRECT_URI,
        'client_id': os.getenv('SPOTIFY_CLIENT_ID', ''), 'client_secret': os.getenv('SPOTIFY_CLIENT_SECRET', ''),
    }, timeout=15); response.raise_for_status(); return response.json()

def refresh_user_token(connection):
    response = requests.post('https://accounts.spotify.com/api/token', data={
        'grant_type': 'refresh_token', 'refresh_token': connection.refresh_token,
        'client_id': os.getenv('SPOTIFY_CLIENT_ID', ''), 'client_secret': os.getenv('SPOTIFY_CLIENT_SECRET', ''),
    }, timeout=15); response.raise_for_status(); data = response.json()
    connection.access_token = data['access_token']; connection.expires_at = timezone.now() + timedelta(seconds=data['expires_in'])
    if data.get('refresh_token'): connection.refresh_token = data['refresh_token']
    connection.save(update_fields=['access_token','refresh_token','expires_at','updated_at']); return connection.access_token

def user_api(connection, method, path, json_data=None):
    token = refresh_user_token(connection) if connection.expired else connection.access_token
    response = requests.request(method, f'https://api.spotify.com/v1{path}', headers={'Authorization': f'Bearer {token}'}, json=json_data, timeout=20)
    if response.status_code == 401:
        token = refresh_user_token(connection)
        response = requests.request(method, f'https://api.spotify.com/v1{path}', headers={'Authorization': f'Bearer {token}'}, json=json_data, timeout=20)
    response.raise_for_status(); return response.json() if response.content else {}
