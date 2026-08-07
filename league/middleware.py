from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse

from .models import UserProfile


class ApprovalRequiredMiddleware:
    """Keep unapproved members on waiting, notification, logout, and legal routes."""

    allowed_names = {
        'waiting_approval', 'notification_settings', 'push_subscribe', 'push_unsubscribe',
        'logout', 'terms', 'privacy', 'service_worker', 'health',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.is_staff and not request.user.is_superuser:
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            if not profile.approved:
                try:
                    url_name = resolve(request.path_info).url_name
                except Resolver404:
                    url_name = None
                if url_name not in self.allowed_names:
                    return redirect(reverse('waiting_approval'))
        return self.get_response(request)
