from django.contrib.auth.models import User
from django.db import models
from django.db.models import Count

from ..models import Badge, Season


def players(query=''):
    queryset = User.objects.select_related('profile').annotate(
        play_count=Count('submissions', distinct=True),
    ).order_by('-date_joined')
    if query:
        queryset = queryset.filter(
            models.Q(username__icontains=query)
            | models.Q(first_name__icontains=query)
            | models.Q(last_name__icontains=query)
            | models.Q(email__icontains=query)
        )
    return queryset


def badges(query=''):
    queryset = Badge.objects.prefetch_related('awards').order_by(
        'sort_order', 'name',
    )
    if query:
        queryset = queryset.filter(
            models.Q(name__icontains=query)
            | models.Q(description__icontains=query)
            | models.Q(achievement_key__icontains=query)
        )
    return queryset


def seasons():
    return Season.objects.annotate(
        round_count=Count('rounds', distinct=True),
    ).order_by('-starts_at')
