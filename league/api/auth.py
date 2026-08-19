from functools import wraps

from .responses import error


def api_methods(*allowed_methods):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in allowed_methods:
                response = error(
                    'method_not_allowed',
                    'This HTTP method is not allowed.',
                    405,
                )
                response['Allow'] = ', '.join(allowed_methods)
                return response
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def api_user_required(view=None, *, allow_pending=False):
    def decorator(func):
        @wraps(func)
        def wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated or not user.is_active:
                return error(
                    'authentication_required',
                    'Authentication is required.',
                    401,
                )
            approved = (
                user.is_staff
                or user.is_superuser
                or user.profile.approved
            )
            if not approved and not allow_pending:
                return error(
                    'approval_required',
                    'League approval is required.',
                    403,
                )
            return func(request, *args, **kwargs)
        return wrapped
    return decorator(view) if view else decorator


def api_staff_required(view):
    @wraps(view)
    @api_user_required
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            return error('staff_required', 'Staff access is required.', 403)
        return view(request, *args, **kwargs)
    return wrapped
