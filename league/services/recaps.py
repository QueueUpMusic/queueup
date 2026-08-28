"""Read models for the private, post-season QueueUp recap experience."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt

from django.contrib.auth.models import User
from django.db.models import Avg
from django.utils import timezone

from ..genres import main_genre
from ..models import Season, SeasonRecapView, Submission, Vote
from ..ranking import ranked_submissions
from ..voting import counted_vote_ids_for_rounds
from .scoring import season_leaderboard


class RecapUnavailable(Exception):
    pass


@dataclass(frozen=True)
class SeasonRecap:
    season: object
    slides: list
    summary: dict


def recap_is_available(season, now=None):
    """A season is safe only after it ended and every published round revealed."""
    now = now or timezone.now()
    return (
        season.ends_at <= now
        and not season.rounds.filter(is_draft=False, reveal_at__gt=now).exists()
    )


def recap_eligible_user_ids(season, now=None):
    """Participants submitted, or cast at least one authoritative counted vote."""
    if not recap_is_available(season, now=now):
        return set()
    rounds = list(season.rounds.filter(is_draft=False))
    counted_ids = counted_vote_ids_for_rounds(rounds, now=now)
    submitted = set(Submission.objects.filter(round__in=rounds).values_list('user_id', flat=True))
    voted = set(Vote.objects.filter(id__in=counted_ids).values_list('voter_id', flat=True))
    return submitted | voted


def user_has_recap(season, user, now=None):
    """Whether this user can open a personal recap for this season."""
    return user.id in recap_eligible_user_ids(season, now=now)


def recap_has_been_viewed(season, user):
    return SeasonRecapView.objects.filter(season=season, user=user).exists()


def mark_recap_viewed(season, user):
    """Persist a successful recap opening; safe to call on every page load."""
    return SeasonRecapView.objects.get_or_create(season=season, user=user)


def most_recent_unseen_recap_for_user(user, now=None):
    """Return one banner-worthy recap, newest first, without stacking banners."""
    now = now or timezone.now()
    viewed_season_ids = SeasonRecapView.objects.filter(user=user).values_list('season_id', flat=True)
    for season in Season.objects.filter(ends_at__lte=now).exclude(pk__in=viewed_season_ids).order_by('-ends_at', '-id'):
        if user_has_recap(season, user, now=now):
            return season
    return None


def _ordinal(place):
    suffix = 'th' if 10 <= place % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(place % 10, 'th')
    return f'{place}{suffix}'


def _song_payload(song, **extra):
    return {
        'title': song.title, 'artist': song.artist, 'art': song.album_art_url,
        'album': song.album, **extra,
    }


def _winner(rows, key):
    """Stable max: primary metric first, then normalized display text, then pk."""
    return sorted(rows, key=key)[0] if rows else None


def season_recap_for_user(season, user, now=None):
    """Build concise, presentation-ready recap data without duplicating game rules."""
    now = now or timezone.now()
    if not recap_is_available(season, now=now):
        raise RecapUnavailable

    rounds = list(season.rounds.filter(is_draft=False).order_by('reveal_at').prefetch_related('submissions'))
    counted_ids = counted_vote_ids_for_rounds(rounds, now=now)
    submissions = list(Submission.objects.filter(round__in=rounds, user=user).select_related('round'))
    my_votes = list(Vote.objects.filter(id__in=counted_ids, voter=user).select_related('submission', 'submission__round'))
    if not submissions and not my_votes:
        raise RecapUnavailable

    leaderboard = season_leaderboard(season, now=now)
    standing = next((row for row in leaderboard if row.item.id == user.id), None)
    standings_size = len(leaderboard)

    rankings = {}
    submission_results = []
    for submission in submissions:
        ranked = rankings.setdefault(submission.round_id, ranked_submissions(submission.round))
        entry = next(row for row in ranked if row.item.id == submission.id)
        scores = [vote.score for vote in Vote.objects.filter(submission=submission, id__in=counted_ids)]
        submission_results.append((submission, entry, scores))

    podiums = sum(entry.place <= 3 for _, entry, _ in submission_results)
    wins = sum(entry.place == 1 for _, entry, _ in submission_results)
    top_half = sum(entry.place <= max(1, len(rankings[submission.round_id]) / 2) for submission, entry, _ in submission_results)
    best_submission = _winner(
        submission_results,
        lambda value: (-float(value[1].score), value[1].place, value[0].title.lower(), value[0].id),
    )

    rated_songs = defaultdict(list)
    artists = defaultdict(list)
    genres = defaultdict(list)
    for vote in my_votes:
        song = vote.submission
        # Counted IDs already enforce both completion and the no-self-vote rule.
        rated_songs[song.id].append(vote)
        artists[song.artist].append(vote)
        if (genre := main_genre(song.genres)):
            genres[genre].append(vote)

    song_candidates = []
    for votes in rated_songs.values():
        song = votes[0].submission
        song_candidates.append((song, sum(v.score for v in votes) / len(votes), len(votes)))
    song_of_season = _winner(song_candidates, lambda value: (-value[1], -value[2], value[0].title.lower(), value[0].artist.lower(), value[0].id))

    repeated_artists = [(name, votes) for name, votes in artists.items() if len(votes) > 1]
    artist_pool = repeated_artists or list(artists.items())
    favorite_artist = _winner(
        artist_pool,
        lambda value: (-sum(v.score for v in value[1]) / len(value[1]), -len(value[1]), value[0].lower()),
    )
    favorite_genre = _winner(
        list(genres.items()),
        lambda value: (-sum(v.score for v in value[1]) / len(value[1]), -len(value[1]), value[0]),
    )

    scores_given = [vote.score for vote in my_votes]
    league_average = Vote.objects.filter(id__in=counted_ids).aggregate(value=Avg('score'))['value']
    five_stars = sum(score == 5 for score in scores_given)
    personality = None
    if len(scores_given) >= 6:
        average = sum(scores_given) / len(scores_given)
        personality = 'Generous listener' if average >= 4.15 else 'Tough critic' if average <= 2.85 else 'Balanced voter'

    chaos = None
    chaos_candidates = []
    for submission, entry, scores in submission_results:
        if len(scores) >= 3 and len(set(scores)) > 1:
            mean = sum(scores) / len(scores)
            deviation = sqrt(sum((score - mean) ** 2 for score in scores) / len(scores))
            chaos_candidates.append((submission, entry, scores, deviation))
    if chaos_candidates:
        chaos = _winner(chaos_candidates, lambda value: (-value[3], -max(value[2]) + min(value[2]), value[0].title.lower(), value[0].id))

    league_songs = list(Submission.objects.filter(round__in=rounds))
    league_ranked = []
    for round_obj in rounds:
        league_ranked.extend(ranked_submissions(round_obj))
    league_best = _winner(league_ranked, lambda value: (-float(value.score), value.item.title.lower(), value.item.id))

    slides = [
        {'kind': 'intro', 'round_count': len(rounds), 'song_count': len(league_songs)},
    ]
    if standing:
        slides.append({'kind': 'standing', 'place': standing.place, 'place_label': _ordinal(standing.place), 'league_size': standings_size,
                       'points': standing.item.total_score, 'podiums': podiums, 'wins': wins,
                       'top_half': top_half, 'played': len(submission_results)})
    if best_submission:
        submission, entry, scores = best_submission
        slides.append({'kind': 'best_submission', 'song': _song_payload(submission, prompt=submission.round.prompt,
                       place=entry.place, place_label=entry.label, average=round(float(entry.score), 1), ratings=len(scores))})
    if song_of_season or favorite_artist or favorite_genre:
        slides.append({'kind': 'taste', 'song': _song_payload(song_of_season[0]) if song_of_season else None,
                       'favorite_artist': favorite_artist[0] if favorite_artist else None,
                       'favorite_genre': favorite_genre[0].title() if favorite_genre else None})
    if scores_given:
        slides.append({'kind': 'voting', 'average': round(sum(scores_given) / len(scores_given), 1),
                       'league_average': round(float(league_average), 1) if league_average is not None else None,
                       'five_stars': five_stars, 'ratings': len(scores_given), 'personality': personality})
    if submission_results:
        strongest = _winner(submission_results, lambda value: (value[1].place, -float(value[1].score), value[0].title.lower(), value[0].id))
        slides.append({'kind': 'story', 'best_finish': strongest[1].label, 'round_prompt': strongest[0].round.prompt,
                       'top_half': top_half, 'played': len(submission_results)})
    if chaos:
        submission, entry, scores, deviation = chaos
        slides.append({'kind': 'chaos', 'song': _song_payload(submission, low=min(scores), high=max(scores),
                       average=round(sum(scores) / len(scores), 1), disagreement=round(deviation, 1))})
    if league_best:
        slides.append({'kind': 'league', 'song_count': len(league_songs), 'rating_count': len(counted_ids),
                       'round_count': len(rounds), 'top_song': _song_payload(league_best.item, average=round(float(league_best.score), 1))})

    summary = {'standing': standing, 'best_submission': best_submission[0] if best_submission else None,
               'song_of_season': song_of_season[0] if song_of_season else None,
               'favorite_artist': favorite_artist[0] if favorite_artist else None,
               'podiums': podiums, 'wins': wins, 'round_count': len(rounds)}
    slides.append({'kind': 'summary', **summary})
    return SeasonRecap(season=season, slides=slides[:10], summary=summary)
