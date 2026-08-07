from django.contrib import admin
from .models import (AchievementUnlock, Badge, NotificationDelivery, PushSubscription, Round, Season,
                     SeasonWelcome, SpotifyConnection, Submission, UserBadge, UserProfile, Vote)

@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ('name', 'starts_at', 'ends_at', 'active')

@admin.register(Round)
class RoundAdmin(admin.ModelAdmin):
    list_display = ('prompt', 'season', 'goes_live_at', 'submission_deadline', 'voting_deadline', 'reveal_at')
    list_filter = ('season',)

@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('title', 'artist', 'user', 'round', 'explicit', 'submitted_at')
    list_filter = ('explicit',)
    search_fields = ('title', 'artist', 'user__username')

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'approved', 'approved_at', 'voting_guide_seen', 'submission_rules_accepted_at')
    list_filter = ('approved', 'voting_guide_seen')
    search_fields = ('user__username', 'user__first_name', 'user__email')

@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'achievement_key', 'hidden', 'display_next_to_name', 'active')
    list_filter = ('hidden', 'display_next_to_name', 'active')

admin.site.register([Vote, PushSubscription, SpotifyConnection, AchievementUnlock,
                     NotificationDelivery, UserBadge, SeasonWelcome])
