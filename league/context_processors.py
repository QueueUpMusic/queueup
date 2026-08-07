from django.utils import timezone

from .achievements import prestige_badges
from .models import Season


def queueup_context(request):
    if not request.user.is_authenticated:
        return {}
    profile = getattr(request.user, 'profile', None)
    if not request.user.is_staff and (not profile or not profile.approved):
        return {}
    season = Season.objects.filter(active=True, starts_at__lte=timezone.now(), ends_at__gte=timezone.now()).order_by('-starts_at').first()
    welcome = None
    if season and not request.user.season_welcomes.filter(season=season).exists():
        welcome = season
    return {'season_welcome': welcome, 'current_user_prestige_badges': prestige_badges(request.user)}
