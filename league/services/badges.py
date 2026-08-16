from dataclasses import dataclass

from ..models import UserBadge


@dataclass(frozen=True)
class BadgeMutationResult:
    awarded: bool
    award: object = None


def toggle_badge_award(user, badge, awarded_by):
    """Toggle the existing manual badge award record for a user."""
    award, created = UserBadge.objects.get_or_create(
        user=user,
        badge=badge,
        defaults={'awarded_by': awarded_by},
    )
    if not created:
        award.delete()
        return BadgeMutationResult(awarded=False)
    return BadgeMutationResult(awarded=True, award=award)
