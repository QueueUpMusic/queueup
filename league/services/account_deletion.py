"""Transactional, reusable account deletion and history anonymization."""

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction

from ..models import AnonymousParticipant, Submission, UserProfile, Vote


User = get_user_model()


def _delete_user_sessions(user_id):
    """Remove every session that still authenticates the deleted user."""
    for session in Session.objects.all().only('session_key', 'session_data'):
        if str(session.get_decoded().get('_auth_user_id')) == str(user_id):
            session.delete()


@transaction.atomic
def delete_account(user):
    """Delete personal records and retain only unlinked anonymous league history.

    The anonymous participant is intentionally created without a user, username,
    email, or deletion token. It exists only to keep one deleted person's ballot
    separate from another deleted person's ballot.
    """
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    anonymous = AnonymousParticipant.objects.create()

    picture_name = ''
    profile = UserProfile.objects.filter(user=locked_user).first()
    if profile and profile.picture:
        picture_name = profile.picture.name

    Submission.objects.filter(user=locked_user).update(
        user=None, anonymous_participant=anonymous,
    )
    Vote.objects.filter(voter=locked_user).update(
        voter=None, anonymous_participant=anonymous,
    )

    _delete_user_sessions(locked_user.pk)
    locked_user.delete()

    if picture_name and profile:
        storage = profile.picture.storage
        transaction.on_commit(lambda: storage.delete(picture_name))

    return anonymous
