from ..models import HomepageCountdown


def active_homepage_countdown():
    """Return the newest active countdown; the ordering is deterministic."""
    return HomepageCountdown.objects.filter(active=True).order_by('-updated_at', '-pk').first()
