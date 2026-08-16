from django.http import JsonResponse
from django.views.csrf import csrf_failure as django_csrf_failure


def success(data=None, status=200):
    return JsonResponse({'ok': True, 'data': data or {}}, status=status)


def error(code, message, status):
    return JsonResponse({
        'ok': False,
        'error': {'code': code, 'message': message},
    }, status=status)


def csrf_failure(request, reason=''):
    if request.path_info.startswith('/api/'):
        return error(
            'csrf_failed',
            'A valid CSRF token is required for this request.',
            403,
        )
    return django_csrf_failure(request, reason=reason)
