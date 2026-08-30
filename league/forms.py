from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Badge, HomepageCountdown, NotificationBlast, Round, Season, UserProfile, Vote
from .services.media import validate_profile_picture


class SignupForm(UserCreationForm):
    display_name = forms.CharField(max_length=150, label='Display name')
    email = forms.EmailField(required=True)
    agree_to_terms = forms.BooleanField(label='I agree to the Terms of Use and Privacy Policy')

    class Meta:
        model = User
        fields = ['display_name', 'username', 'email', 'password1', 'password2', 'agree_to_terms']

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data['display_name'].strip()
        user.email = self.cleaned_data['email'].strip()
        if commit:
            user.save()
        return user


class VoteForm(forms.ModelForm):
    class Meta:
        model = Vote
        fields = ['score']
        widgets = {'score': forms.RadioSelect(choices=[(i, f'{i} star' + ('s' if i != 1 else '')) for i in range(1, 6)])}


class SeasonForm(forms.ModelForm):
    class Meta:
        model = Season
        fields = ['name', 'description', 'banner', 'starts_at', 'ends_at', 'active']
        widgets = {'starts_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}), 'ends_at': forms.DateTimeInput(attrs={'type': 'datetime-local'})}


class RoundForm(forms.ModelForm):
    class Meta:
        model = Round
        fields = ['season', 'prompt', 'details', 'goes_live_at', 'submission_opens', 'submission_deadline', 'voting_deadline', 'reveal_at', 'host', 'playlist_url']
        widgets = {
            'details': forms.Textarea(attrs={'rows': 3}),
            'goes_live_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'submission_opens': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'submission_deadline': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'voting_deadline': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'reveal_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def clean(self):
        cleaned = super().clean()
        live = cleaned.get('goes_live_at')
        opens = cleaned.get('submission_opens')
        deadline = cleaned.get('submission_deadline')
        voting = cleaned.get('voting_deadline')
        reveal = cleaned.get('reveal_at')
        if live and opens and live > opens:
            self.add_error('goes_live_at', 'The round must go live before submissions open.')
        if opens and deadline and opens >= deadline:
            self.add_error('submission_deadline', 'Submission deadline must be after submissions open.')
        if deadline and voting and deadline >= voting:
            self.add_error('voting_deadline', 'Voting deadline must be after the submission deadline.')
        if voting and reveal and voting > reveal:
            self.add_error('reveal_at', 'Reveal must be at or after the voting deadline.')
        return cleaned


class ProfileForm(forms.Form):
    display_name = forms.CharField(max_length=150, label='Display name')

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user and not self.is_bound:
            self.fields['display_name'].initial = user.first_name or user.username

    def save(self):
        self.user.first_name = self.cleaned_data['display_name'].strip()
        self.user.save(update_fields=['first_name'])
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        return profile


class ProfilePictureForm(forms.Form):
    picture = forms.ImageField(
        label='Profile picture',
        help_text='JPG/JPEG, HEIC/HEIF, PNG, GIF, or WebP. Maximum 5 MB.',
    )

    def clean_picture(self):
        return validate_profile_picture(self.cleaned_data['picture'])


class BadgeForm(forms.ModelForm):
    class Meta:
        model = Badge
        fields = ['name', 'slug', 'description', 'icon', 'achievement_key', 'hidden', 'display_next_to_name', 'active', 'sort_order']


class HomepageCountdownForm(forms.ModelForm):
    class Meta:
        model = HomepageCountdown
        fields = ['title', 'target_at', 'active']
        widgets = {'target_at': forms.DateTimeInput(attrs={'type': 'datetime-local'})}

    def clean_target_at(self):
        value = self.cleaned_data['target_at']
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return value


class NotificationBlastForm(forms.ModelForm):
    destination = forms.CharField(required=False, max_length=500, label='Destination path')

    class Meta:
        model = NotificationBlast
        fields = ['title', 'body', 'destination', 'audience', 'scheduled_for']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 5}),
            'scheduled_for': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def clean_destination(self):
        value = (self.cleaned_data.get('destination') or '').strip() or '/home/'
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if not value.startswith('/') or value.startswith('//') or '\\' in value or parsed.scheme or parsed.netloc:
            raise forms.ValidationError('Use an internal QueueUp path beginning with /.')
        return value

    def clean_scheduled_for(self):
        value = self.cleaned_data.get('scheduled_for')
        if value and timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        if value and value <= timezone.now():
            raise forms.ValidationError('Scheduled time must be in the future.')
        return value
