from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg
from django.utils import timezone


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    picture = models.ImageField(upload_to='profile_pictures/%Y/%m/', blank=True)
    approved = models.BooleanField(default=False, help_text='Approved users may enter the league.')
    approved_at = models.DateTimeField(null=True, blank=True)
    voting_guide_seen = models.BooleanField(default=False)
    submission_rules_accepted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Profile for {self.user.username}'


class Season(models.Model):
    name = models.CharField(max_length=120)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    active = models.BooleanField(default=True)
    description = models.TextField(blank=True, help_text='Shown in the new-season welcome.')
    banner = models.ImageField(upload_to='season_banners/%Y/%m/', blank=True)

    def __str__(self):
        return self.name


class SeasonWelcome(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='season_welcomes')
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='welcomed_users')
    seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'season'], name='one_welcome_per_user_season')]


class SeasonRecapView(models.Model):
    """Records that a player opened one personal season recap."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='season_recap_views')
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='recap_views')
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'season'], name='one_recap_view_per_user_season')]


class HomepageCountdown(models.Model):
    title = models.CharField(max_length=160)
    target_at = models.DateTimeField()
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_countdowns')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-pk']

    def __str__(self):
        return self.title


class NotificationBlast(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SCHEDULED = 'scheduled', 'Scheduled'
        SENDING = 'sending', 'Sending'
        SENT = 'sent', 'Sent'
        CANCELLED = 'cancelled', 'Cancelled'

    class Audience(models.TextChoices):
        APPROVED = 'approved', 'All approved players'

    title = models.CharField(max_length=120)
    body = models.TextField(max_length=1000)
    destination = models.CharField(max_length=500, default='/home/')
    audience = models.CharField(max_length=24, choices=Audience.choices, default=Audience.APPROVED)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_notification_blasts')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return self.title


class Badge(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.CharField(max_length=240)
    icon = models.CharField(max_length=16, default='◆', help_text='Emoji or short symbol.')
    achievement_key = models.CharField(max_length=80, blank=True, help_text='Optional automatic achievement key.')
    hidden = models.BooleanField(default=False)
    display_next_to_name = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class UserBadge(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='badge_awards')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name='awards')
    awarded_at = models.DateTimeField(auto_now_add=True)
    awarded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='badges_given')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'badge'], name='one_badge_award_per_user')]

    def __str__(self):
        return f'{self.user.username}: {self.badge.name}'


class Round(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='rounds')
    prompt = models.CharField(max_length=240)
    details = models.TextField(blank=True)
    goes_live_at = models.DateTimeField(null=True, blank=True, help_text='Players cannot see this round before this time.')
    submission_opens = models.DateTimeField()
    submission_deadline = models.DateTimeField()
    voting_deadline = models.DateTimeField()
    reveal_at = models.DateTimeField()
    host = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='hosted_rounds')
    playlist_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    announcement_notification_sent = models.BooleanField(default=False)
    submission_notification_sent = models.BooleanField(default=False)
    submission_reminder_notification_sent = models.BooleanField(default=False)
    voting_notification_sent = models.BooleanField(default=False)
    voting_reminder_notification_sent = models.BooleanField(default=False)
    reveal_notification_sent = models.BooleanField(default=False)
    archived = models.BooleanField(default=False, help_text='Hide this completed round from the homepage while keeping it in the archive.')
    is_draft = models.BooleanField(default=False, help_text='Keep this round staff-only until it is saved or published.')

    class Meta:
        ordering = ['-submission_opens']

    @property
    def is_visible(self):
        return not self.is_draft and (not self.goes_live_at or self.goes_live_at <= timezone.now())

    @property
    def state(self):
        now = timezone.now()
        if self.is_draft:
            return 'draft'
        if not self.is_visible:
            return 'hidden'
        if now < self.submission_opens:
            return 'upcoming'
        if now < self.submission_deadline:
            return 'submitting'
        if now < self.voting_deadline:
            return 'voting'
        if now < self.reveal_at:
            return 'locked'
        return 'revealed'

    def __str__(self):
        return f'{self.season}: {self.prompt}'


class Submission(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='submissions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submissions')
    spotify_track_id = models.CharField(max_length=128)
    isrc = models.CharField(max_length=32, null=True, blank=True, db_index=True)
    spotify_uri = models.CharField(max_length=200)
    spotify_url = models.URLField()
    title = models.CharField(max_length=240)
    artist = models.CharField(max_length=240)
    artist_ids = models.JSONField(default=list, blank=True)
    genres = models.JSONField(default=list, blank=True)
    album = models.CharField(max_length=240, blank=True)
    album_art_url = models.URLField(blank=True)
    preview_url = models.URLField(blank=True)
    explicit = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['round', 'user'], name='one_submission_per_user_round'),
            models.UniqueConstraint(
                fields=['round', 'isrc'],
                condition=models.Q(isrc__isnull=False),
                name='no_duplicate_isrc_per_round',
            ),
        ]

    @property
    def average_score(self):
        from .voting import counted_vote_ids_for_round
        counted_ids = counted_vote_ids_for_round(self.round)
        return self.votes.filter(id__in=counted_ids).aggregate(v=Avg('score'))['v'] or 0

    def __str__(self):
        return f'{self.title} — {self.artist}'


class Vote(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='votes')
    voter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='votes')
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name='votes')
    score = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['voter', 'submission'], name='one_vote_per_submission')]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.submission_id and self.round_id != self.submission.round_id:
            raise ValidationError('Round mismatch.')
        if self.submission_id and self.voter_id == self.submission.user_id:
            raise ValidationError('You cannot vote for your own song.')


class AchievementUnlock(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='achievement_unlocks')
    key = models.CharField(max_length=80)
    earned_at = models.DateTimeField(auto_now_add=True)
    notification_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'key'], name='one_unlock_per_user_achievement')]

    def __str__(self):
        return f'{self.user.username}: {self.key}'


class PushSubscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.TextField(unique=True)
    p256dh = models.TextField()
    auth = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Push subscription for {self.user}'


class NotificationDelivery(models.Model):
    subscription = models.ForeignKey(PushSubscription, on_delete=models.CASCADE, related_name='deliveries')
    event_key = models.CharField(max_length=200)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['subscription', 'event_key'], name='one_push_delivery_per_event')]

    def __str__(self):
        return f'{self.event_key} -> {self.subscription_id}'


class SpotifyConnection(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='spotify_connection')
    spotify_user_id = models.CharField(max_length=128, blank=True)
    display_name = models.CharField(max_length=200, blank=True)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_at = models.DateTimeField()
    scope = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def expired(self):
        return self.expires_at <= timezone.now() + timezone.timedelta(seconds=60)

    def __str__(self):
        return f'Spotify: {self.display_name or self.user.username}'
