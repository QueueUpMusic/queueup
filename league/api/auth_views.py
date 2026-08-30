import json

from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.middleware.csrf import get_token

from ..forms import SignupForm
from ..services import membership as membership_service
from .auth import api_methods, api_user_required
from .responses import error, success
from .serializers import session_user


def _json_body(request):
    try:
        value = json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _session_response(user):
    return {
        'authenticated': True,
        'user': session_user(user),
    }


def _form_error_response(form):
    return error(
        'validation_failed',
        'Form validation failed.',
        400,
        errors=form.errors.get_json_data(),
    )


@api_methods('GET')
def csrf_token(request):
    return success({'csrf_token': get_token(request)})


@api_methods('POST')
def login_user(request):
    body = _json_body(request)
    if body is None:
        return error('invalid_json', 'A JSON object is required.', 400)

    form = AuthenticationForm(request=request, data={
        'username': body.get('username', ''),
        'password': body.get('password', ''),
    })
    if not form.is_valid():
        return _form_error_response(form)

    user = form.get_user()
    login(request, user)
    return success(_session_response(user))


@api_methods('POST')
def signup(request):
    body = _json_body(request)
    if body is None:
        return error('invalid_json', 'A JSON object is required.', 400)

    form_data = {
        'display_name': body.get('display_name', ''),
        'username': body.get('username', ''),
        'email': body.get('email', ''),
        'password1': body.get('password', ''),
        'password2': body.get('password_confirm', ''),
        # Identity comparison keeps terms opt-in: JSON 1, "true", etc. do
        # not satisfy the mobile contract's explicit boolean requirement.
        'agree_to_terms': body.get('agree_to_terms') is True,
    }
    form = SignupForm(form_data)
    if not form.is_valid():
        return _form_error_response(form)

    user = form.save()
    profile = membership_service.initialize_user_membership(user)
    login(request, user)
    return success(_session_response(user))


@api_methods('POST')
@api_user_required(allow_pending=True)
def logout_user(request):
    logout(request)
    return success({'authenticated': False, 'user': None})
