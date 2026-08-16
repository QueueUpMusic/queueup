from dataclasses import dataclass

from django.utils import timezone

from ..models import UserProfile


class UnknownMembershipAction(Exception):
    pass


class SelfAccessChangeNotAllowed(Exception):
    pass


@dataclass(frozen=True)
class MembershipMutationResult:
    target: object
    approved_now: bool = False


def initialize_user_membership(user, now=None):
    """Apply the existing approval state assigned to a new account."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.approved = bool(user.is_staff or user.is_superuser)
    profile.approved_at = (now or timezone.now()) if profile.approved else None
    profile.save(update_fields=['approved', 'approved_at'])
    return profile


def apply_membership_action(target, action, actor, now=None):
    """Apply one existing staff membership command to a user."""
    removing_own_access = target == actor and (
        (action == 'toggle_active' and target.is_active)
        or (action == 'toggle_staff' and target.is_staff)
    )
    if removing_own_access:
        raise SelfAccessChangeNotAllowed

    profile, _ = UserProfile.objects.get_or_create(user=target)
    if action == 'toggle_staff':
        target.is_staff = not target.is_staff
        if target.is_staff and not profile.approved:
            profile.approved = True
            profile.approved_at = now or timezone.now()
            profile.save(update_fields=['approved', 'approved_at'])
        target.save(update_fields=['is_staff', 'is_active'])
        return MembershipMutationResult(target=target)

    if action == 'toggle_active':
        target.is_active = not target.is_active
        target.save(update_fields=['is_staff', 'is_active'])
        return MembershipMutationResult(target=target)

    if action == 'approve':
        approved_now = False
        if not profile.approved:
            profile.approved = True
            profile.approved_at = now or timezone.now()
            profile.save(update_fields=['approved', 'approved_at'])
            approved_now = True
        return MembershipMutationResult(
            target=target,
            approved_now=approved_now,
        )

    raise UnknownMembershipAction
