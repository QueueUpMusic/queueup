from django.views.decorators.csrf import ensure_csrf_cookie

from .auth import api_methods, api_user_required
from .responses import success
from .serializers import session_user


@api_methods('GET')
@api_user_required
def api_index(request):
    return success({'version': 'v1'})


@ensure_csrf_cookie
@api_methods('GET')
@api_user_required(allow_pending=True)
def current_session(request):
    user = request.user
    return success({
        'authenticated': True,
        'user': session_user(user),
    })
