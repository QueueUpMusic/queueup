from django.views.decorators.csrf import ensure_csrf_cookie

from .auth import api_methods, api_user_required
from .responses import success


@api_methods('GET')
@api_user_required
def api_index(request):
    return success({'version': 'v1'})


@ensure_csrf_cookie
@api_methods('GET')
@api_user_required(allow_pending=True)
def current_session(request):
    user = request.user
    approved = user.is_staff or user.is_superuser or user.profile.approved
    return success({
        'authenticated': True,
        'user': {
            'id': user.pk,
            'username': user.username,
            'display_name': user.first_name or user.username,
            'email': user.email,
            'approved': approved,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
        },
    })
