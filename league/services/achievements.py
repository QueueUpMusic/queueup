from ..genres import main_genre
from ..models import Badge, UserBadge
from .profiles import profile_metrics


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


def achievement_checks(user):
    metrics = profile_metrics(user)
    submissions = metrics.submissions
    revealed = metrics.revealed
    # Genre Hopper intentionally counts one normalized main genre per song and
    # only considers submissions whose round has been revealed.
    genres = {
        genre
        for submission in revealed
        if (genre := main_genre(submission.genres))
    }
    return {
        'first_pick': submissions.exists(),
        'first_win': metrics.wins >= 1,
        'five_rounds': submissions.count() >= 5,
        'ten_rounds': submissions.count() >= 10,
        'twenty_five_rounds': submissions.count() >= 25,
        'fifty_rounds': submissions.count() >= 50,
        'perfect_score': revealed.filter(avg__gte=5).exists(),
        'taste_maker': metrics.podiums >= 3,
        'podium_ten': metrics.podiums >= 10,
        'five_wins': metrics.wins >= 5,
        'ten_wins': metrics.wins >= 10,
        'deep_cut': submissions.values('artist').distinct().count() >= 10,
        'three_seasons': (
            submissions.values('round__season').distinct().count() >= 3
        ),
        'five_seasons': (
            submissions.values('round__season').distinct().count() >= 5
        ),
        'genre_hopper': len(genres) >= 10,
        'close_call': metrics.ties_for_first >= 1,
    }


def earned_badges(user):
    checks = achievement_checks(user)
    rows = [{
        'key': key,
        'name': name,
        'description': description,
        'icon': icon,
        'earned': checks.get(key, False),
        'hidden': hidden,
    } for key, name, description, icon, hidden in ACHIEVEMENTS]
    custom_awards = {
        award.badge_id
        for award in UserBadge.objects.filter(user=user).select_related('badge')
    }
    for badge in Badge.objects.filter(active=True):
        if badge.achievement_key in checks:
            continue
        earned = badge.id in custom_awards
        rows.append({
            'key': f'badge:{badge.slug}',
            'name': badge.name,
            'description': badge.description,
            'icon': badge.icon,
            'earned': earned,
            'hidden': badge.hidden,
        })
    return rows


def prestige_badges(user, limit=3):
    checks = achievement_checks(user)
    automatic = list(
        Badge.objects.filter(
            active=True,
            display_next_to_name=True,
        ).exclude(achievement_key='')
    )
    manual = list(Badge.objects.filter(
        active=True,
        display_next_to_name=True,
        awards__user=user,
    ))
    seen = set()
    result = []
    for badge in manual + automatic:
        if badge.pk in seen:
            continue
        if badge in automatic and not checks.get(badge.achievement_key, False):
            continue
        seen.add(badge.pk)
        result.append(badge)
    return result[:limit]
