from django.db.models import Avg, Count, Q
from django.utils import timezone

from .genres import main_genre
from .models import Badge, UserBadge
from .ranking import ranked_submissions
from .voting import counted_vote_ids_for_rounds

ACHIEVEMENTS = [
    ('first_pick', 'First Pick', 'Submit your first song', '🎵', False),
    ('first_win', 'First Win', 'Win a revealed round', '🏆', False),
    ('five_rounds', 'Regular', 'Play five rounds', '🔥', False),
    ('ten_rounds', 'League Veteran', 'Play ten rounds', '💿', False),
    ('twenty_five_rounds', 'Long Haul', 'Play 25 rounds', '🛣️', False),
    ('fifty_rounds', 'QueueUp Legend', 'Play 50 rounds', '👑', True),
    ('perfect_score', 'Perfect Score', 'Receive a 5.00 average', '⭐', False),
    ('taste_maker', 'Taste Maker', 'Earn three podium finishes', '✨', False),
    ('podium_ten', 'Podium Fixture', 'Earn ten podium finishes', '🥇', True),
    ('five_wins', 'Five-Time Champion', 'Win five rounds', '🏅', True),
    ('ten_wins', 'Dynasty', 'Win ten rounds', '⚜️', True),
    ('deep_cut', 'Deep Cut', 'Submit songs by ten different artists', '🕵️', False),
    ('three_seasons', 'Seasoned', 'Compete in three different seasons', '🌟', True),
    ('five_seasons', 'Founding Myth', 'Compete in five different seasons', '🗿', True),
    ('genre_hopper', 'Genre Hopper', 'Submit songs in ten different main genres', '🌈', True),
    ('close_call', 'Photo Finish', 'Finish tied for first place', '📸', True),
]


def profile_metrics(user, season=None):
    now = timezone.now()
    base_subs = user.submissions.select_related('round', 'round__season')
    if season:
        base_subs = base_subs.filter(round__season=season)
    related_rounds = list({sub.round_id: sub.round for sub in base_subs}.values())
    counted_vote_ids = counted_vote_ids_for_rounds(related_rounds, now=now)
    counted_filter = Q(votes__id__in=counted_vote_ids)
    subs = base_subs.annotate(
        avg=Avg('votes__score', filter=counted_filter),
        vote_count=Count('votes', filter=counted_filter),
    )
    revealed = subs.filter(round__reveal_at__lte=now)
    wins = podiums = ties_for_first = 0
    placements = []
    for sub in revealed:
        ranked = ranked_submissions(sub.round)
        entry = next((row for row in ranked if row.item.id == sub.id), None)
        if entry:
            placements.append(entry.place)
            wins += entry.place == 1
            podiums += entry.place <= 3
            ties_for_first += entry.place == 1 and sum(1 for row in ranked if row.place == 1) > 1
    return {'submissions': subs, 'revealed': revealed, 'wins': wins, 'podiums': podiums, 'placements': placements, 'ties_for_first': ties_for_first, 'counted_vote_ids': counted_vote_ids}


def achievement_checks(user):
    m = profile_metrics(user)
    subs, revealed = m['submissions'], m['revealed']
    # Count one broad/main genre per song, and only after that round is revealed.
    # Spotify supplies multiple artist subgenre tags, which must never let one
    # song count as several genres toward this achievement.
    genres = {genre for sub in revealed if (genre := main_genre(sub.genres))}
    return {
        'first_pick': subs.exists(),
        'first_win': m['wins'] >= 1,
        'five_rounds': subs.count() >= 5,
        'ten_rounds': subs.count() >= 10,
        'twenty_five_rounds': subs.count() >= 25,
        'fifty_rounds': subs.count() >= 50,
        'perfect_score': revealed.filter(avg__gte=5).exists(),
        'taste_maker': m['podiums'] >= 3,
        'podium_ten': m['podiums'] >= 10,
        'five_wins': m['wins'] >= 5,
        'ten_wins': m['wins'] >= 10,
        'deep_cut': subs.values('artist').distinct().count() >= 10,
        'three_seasons': subs.values('round__season').distinct().count() >= 3,
        'five_seasons': subs.values('round__season').distinct().count() >= 5,
        'genre_hopper': len(genres) >= 10,
        'close_call': m['ties_for_first'] >= 1,
    }


def earned_badges(user):
    checks = achievement_checks(user)
    rows = [{
        'key': key, 'name': name, 'description': description, 'icon': icon,
        'earned': checks.get(key, False), 'hidden': hidden,
    } for key, name, description, icon, hidden in ACHIEVEMENTS]
    custom_awards = {award.badge_id for award in UserBadge.objects.filter(user=user).select_related('badge')}
    for badge in Badge.objects.filter(active=True):
        if badge.achievement_key in checks:
            continue
        earned = badge.id in custom_awards
        rows.append({'key': f'badge:{badge.slug}', 'name': badge.name, 'description': badge.description,
                     'icon': badge.icon, 'earned': earned, 'hidden': badge.hidden})
    return rows


def prestige_badges(user, limit=3):
    checks = achievement_checks(user)
    automatic = list(Badge.objects.filter(active=True, display_next_to_name=True).exclude(achievement_key=''))
    manual = list(Badge.objects.filter(active=True, display_next_to_name=True, awards__user=user))
    seen, result = set(), []
    for badge in manual + automatic:
        if badge.pk in seen:
            continue
        if badge in automatic and not checks.get(badge.achievement_key, False):
            continue
        seen.add(badge.pk); result.append(badge)
    return result[:limit]
