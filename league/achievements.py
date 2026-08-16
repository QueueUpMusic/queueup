"""Compatibility imports for the shared achievement/profile service layer."""

from .services.achievements import (
    ACHIEVEMENTS,
    achievement_checks,
    earned_badges,
    prestige_badges,
)
from .services.profiles import profile_metrics as _profile_metrics


def profile_metrics(user, season=None):
    """Preserve the legacy dictionary contract for existing integrations."""
    return _profile_metrics(user, season).as_legacy_dict()


__all__ = [
    'ACHIEVEMENTS',
    'achievement_checks',
    'earned_badges',
    'prestige_badges',
    'profile_metrics',
]
