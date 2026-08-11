import io
import re
from datetime import timedelta
from unittest.mock import patch

from PIL import Image

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.mail import get_connection
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils import timezone

from .models import NotificationDelivery, PushSubscription, Round, Season, SeasonWelcome, Submission, Vote
from .ranking import competition_rank, ranked_submissions, winner_ids
from .voting import voting_progress


class QueueUpTestMixin:
    def setUp(self):
        now = timezone.now()
        self.season = Season.objects.create(name='Season', starts_at=now - timedelta(days=10), ends_at=now + timedelta(days=20))
        self.round = Round.objects.create(
            season=self.season, prompt='Prompt', submission_opens=now - timedelta(days=3),
            submission_deadline=now - timedelta(days=2), voting_deadline=now + timedelta(hours=12),
            reveal_at=now + timedelta(days=1),
        )
        self.alice = User.objects.create_user('alice', password='x')
        self.bob = User.objects.create_user('bob', password='x')
        self.cara = User.objects.create_user('cara', password='x')
        for user in (self.alice, self.bob, self.cara):
            user.profile.approved = True
            user.profile.approved_at = now
            user.profile.save(update_fields=['approved', 'approved_at'])

    def submission(self, user, track):
        return Submission.objects.create(
            round=self.round, user=user, spotify_track_id=track, spotify_uri=f'spotify:track:{track}',
            spotify_url=f'https://open.spotify.com/track/{track}', title=track, artist='Artist',
        )


class SeasonWelcomeTests(QueueUpTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.alice)

    def test_new_user_sees_current_season_welcome(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.context['season_welcome'], self.season)
        self.assertContains(response, 'Enter the season')

    def test_entering_season_creates_welcome_and_hides_prompt(self):
        response = self.client.post(reverse('season_welcome_seen', args=[self.season.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(SeasonWelcome.objects.filter(user=self.alice, season=self.season).exists())
        next_page = self.client.get(reverse('leaderboard'))
        self.assertIsNone(next_page.context['season_welcome'])
        self.assertNotContains(next_page, 'Enter the season')

    def test_entering_season_is_idempotent(self):
        url = reverse('season_welcome_seen', args=[self.season.pk])

        self.client.post(url)
        response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SeasonWelcome.objects.filter(user=self.alice, season=self.season).count(), 1)

    def test_existing_welcome_is_unchanged(self):
        welcome = SeasonWelcome.objects.create(user=self.alice, season=self.season)

        response = self.client.get(reverse('home'))

        self.assertIsNone(response.context['season_welcome'])
        self.assertEqual(SeasonWelcome.objects.get(user=self.alice, season=self.season), welcome)


class VotingTests(QueueUpTestMixin, TestCase):
    def test_completion_after_final_required_vote_and_on_revisit(self):
        own = self.submission(self.alice, 'own')
        bob_song = self.submission(self.bob, 'bob')
        cara_song = self.submission(self.cara, 'cara')
        self.assertFalse(voting_progress(self.round, self.alice)['complete'])
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=4)
        self.assertFalse(voting_progress(self.round, self.alice)['complete'])
        Vote.objects.create(round=self.round, voter=self.alice, submission=cara_song, score=5)
        self.assertTrue(voting_progress(self.round, self.alice)['complete'])
        self.assertNotIn(own.id, voting_progress(self.round, self.alice)['eligible_ids'])
        self.client.force_login(self.alice)
        response = self.client.get(reverse('round_detail', args=[self.round.pk]))
        self.assertContains(response, "You're all caught up!")

    def test_ajax_final_vote_returns_completion_only_after_persisting(self):
        self.submission(self.alice, 'own')
        bob_song = self.submission(self.bob, 'bob')
        self.client.force_login(self.alice)
        response = self.client.post(
            reverse('vote', args=[self.round.pk, bob_song.pk]), {'score': 5},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['complete'])
        self.assertTrue(Vote.objects.filter(voter=self.alice, submission=bob_song, score=5).exists())

    def test_review_page_loads_previously_saved_scores(self):
        self.submission(self.alice, 'own-review')
        bob_song = self.submission(self.bob, 'bob-review')
        cara_song = self.submission(self.cara, 'cara-review')
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=2)
        Vote.objects.create(round=self.round, voter=self.alice, submission=cara_song, score=5)

        self.client.force_login(self.alice)
        response = self.client.get(reverse('round_detail', args=[self.round.pk]) + '?review=1')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="score" value="2"', count=1)
        self.assertContains(response, 'name="score" value="5"', count=1)

    def test_editing_one_vote_does_not_change_other_saved_votes(self):
        self.submission(self.alice, 'own-edit')
        bob_song = self.submission(self.bob, 'bob-edit')
        cara_song = self.submission(self.cara, 'cara-edit')
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=2)
        Vote.objects.create(round=self.round, voter=self.alice, submission=cara_song, score=5)

        self.client.force_login(self.alice)
        response = self.client.post(
            reverse('vote', args=[self.round.pk, bob_song.pk]),
            {'score': 4},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Vote.objects.get(voter=self.alice, submission=bob_song).score, 4)
        self.assertEqual(Vote.objects.get(voter=self.alice, submission=cara_song).score, 5)

    def test_changed_rating_keeps_completion(self):
        self.submission(self.alice, 'own')
        bob_song = self.submission(self.bob, 'bob')
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=2)
        self.client.force_login(self.alice)
        response = self.client.post(reverse('vote', args=[self.round.pk, bob_song.pk]), {'score': 4}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(response.json()['complete'])
        self.assertEqual(Vote.objects.get(voter=self.alice, submission=bob_song).score, 4)


class RankingTests(QueueUpTestMixin, TestCase):
    def test_competition_ranking_and_all_tied_winners(self):
        rows = [type('Row', (), {'score': value, 'name': name})() for name, value in [('A', 50), ('B', 50), ('C', 42), ('D', 30)]]
        ranked = competition_rank(rows)
        self.assertEqual([row.place for row in ranked], [1, 1, 3, 4])
        self.assertTrue(ranked[0].tied)
        a = self.submission(self.alice, 'a')
        b = self.submission(self.bob, 'b')
        c = self.submission(self.cara, 'c')
        for voter in (self.bob, self.cara):
            if voter != a.user: Vote.objects.create(round=self.round, voter=voter, submission=a, score=5)
        for voter in (self.alice, self.cara):
            if voter != b.user: Vote.objects.create(round=self.round, voter=voter, submission=b, score=5)
        Vote.objects.create(round=self.round, voter=self.alice, submission=c, score=3)
        Vote.objects.create(round=self.round, voter=self.bob, submission=c, score=3)
        self.assertEqual(winner_ids(self.round), {a.id, b.id})

    def test_incomplete_ballot_is_excluded_after_voting_deadline(self):
        alice_song = self.submission(self.alice, 'alice-song')
        bob_song = self.submission(self.bob, 'bob-song')
        cara_song = self.submission(self.cara, 'cara-song')

        # Alice completes both eligible ratings.
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=4)
        Vote.objects.create(round=self.round, voter=self.alice, submission=cara_song, score=5)

        # Bob rates only one of his two eligible songs, so his whole ballot
        # must be ignored after the deadline.
        Vote.objects.create(round=self.round, voter=self.bob, submission=alice_song, score=1)

        self.round.voting_deadline = timezone.now() - timedelta(minutes=1)
        self.round.reveal_at = timezone.now() + timedelta(hours=1)
        self.round.save(update_fields=['voting_deadline', 'reveal_at'])

        ranked = {entry.item.id: entry.item for entry in ranked_submissions(self.round)}
        self.assertEqual(ranked[alice_song.id].vote_count, 0)
        self.assertIsNone(ranked[alice_song.id].avg)
        self.assertEqual(ranked[bob_song.id].vote_count, 1)
        self.assertEqual(ranked[bob_song.id].avg, 4)
        self.assertEqual(ranked[cara_song.id].vote_count, 1)
        self.assertEqual(ranked[cara_song.id].avg, 5)
        self.assertTrue(Vote.objects.filter(voter=self.bob, submission=alice_song).exists())

    def test_partial_votes_still_save_while_voting_is_open(self):
        alice_song = self.submission(self.alice, 'alice-open')
        self.submission(self.bob, 'bob-open')
        self.submission(self.cara, 'cara-open')
        Vote.objects.create(round=self.round, voter=self.bob, submission=alice_song, score=2)

        ranked = {entry.item.id: entry.item for entry in ranked_submissions(self.round)}
        self.assertEqual(ranked[alice_song.id].vote_count, 1)
        self.assertEqual(ranked[alice_song.id].avg, 2)


class HomeRoundVisibilityTests(QueueUpTestMixin, TestCase):
    def test_latest_results_stay_above_new_live_round(self):
        now = timezone.now()
        self.round.submission_opens = now - timedelta(days=5)
        self.round.submission_deadline = now - timedelta(days=4)
        self.round.voting_deadline = now - timedelta(days=3)
        self.round.reveal_at = now - timedelta(minutes=5)
        self.round.prompt = 'Finished prompt'
        self.round.save()

        new_round = Round.objects.create(
            season=self.season,
            prompt='New live prompt',
            goes_live_at=now - timedelta(minutes=1),
            submission_opens=now + timedelta(minutes=5),
            submission_deadline=now + timedelta(days=2),
            voting_deadline=now + timedelta(days=3),
            reveal_at=now + timedelta(days=4),
        )

        self.client.force_login(self.alice)
        response = self.client.get(reverse('home'))

        self.assertEqual(response.context['results_round'], self.round)
        self.assertEqual(response.context['round'], new_round)
        self.assertContains(response, 'Finished prompt')
        self.assertContains(response, 'New live prompt')
        self.assertLess(
            response.content.decode().index('Finished prompt'),
            response.content.decode().index('New live prompt'),
        )

    def test_submitting_round_moves_above_latest_results(self):
        now = timezone.now()
        self.round.submission_opens = now - timedelta(days=5)
        self.round.submission_deadline = now - timedelta(days=4)
        self.round.voting_deadline = now - timedelta(days=3)
        self.round.reveal_at = now - timedelta(minutes=5)
        self.round.prompt = 'Finished prompt'
        self.round.save()

        new_round = Round.objects.create(
            season=self.season,
            prompt='Submitting prompt',
            goes_live_at=now - timedelta(hours=1),
            submission_opens=now - timedelta(minutes=1),
            submission_deadline=now + timedelta(days=2),
            voting_deadline=now + timedelta(days=3),
            reveal_at=now + timedelta(days=4),
        )

        self.client.force_login(self.alice)
        response = self.client.get(reverse('home'))

        self.assertEqual(response.context['round'], new_round)
        self.assertTrue(response.context['current_first'])
        self.assertLess(
            response.content.decode().index('Submitting prompt'),
            response.content.decode().index('Finished prompt'),
        )

    def test_archived_round_leaves_home_but_remains_in_archive(self):
        now = timezone.now()
        self.round.submission_opens = now - timedelta(days=5)
        self.round.submission_deadline = now - timedelta(days=4)
        self.round.voting_deadline = now - timedelta(days=3)
        self.round.reveal_at = now - timedelta(minutes=5)
        self.round.archived = True
        self.round.save()

        self.client.force_login(self.alice)
        home_response = self.client.get(reverse('home'))
        archive_response = self.client.get(reverse('archive'))

        self.assertIsNone(home_response.context['results_round'])
        self.assertNotContains(home_response, self.round.prompt)
        self.assertContains(archive_response, self.round.prompt)

    def test_staff_can_archive_completed_round(self):
        now = timezone.now()
        staff = User.objects.create_user('staffer', password='x', is_staff=True)
        self.round.submission_opens = now - timedelta(days=5)
        self.round.submission_deadline = now - timedelta(days=4)
        self.round.voting_deadline = now - timedelta(days=3)
        self.round.reveal_at = now - timedelta(minutes=5)
        self.round.save()

        self.client.force_login(staff)
        response = self.client.post(reverse('round_archive', args=[self.round.pk]))

        self.assertRedirects(response, reverse('control_panel'))
        self.round.refresh_from_db()
        self.assertTrue(self.round.archived)

    def test_active_round_cannot_be_archived(self):
        staff = User.objects.create_user('staffer2', password='x', is_staff=True)
        self.client.force_login(staff)

        response = self.client.post(reverse('round_archive', args=[self.round.pk]))

        self.assertRedirects(response, reverse('control_panel'))
        self.round.refresh_from_db()
        self.assertFalse(self.round.archived)

    def test_latest_revealed_round_shows_when_no_new_round_exists(self):
        now = timezone.now()
        self.round.submission_opens = now - timedelta(days=5)
        self.round.submission_deadline = now - timedelta(days=4)
        self.round.voting_deadline = now - timedelta(days=3)
        self.round.reveal_at = now - timedelta(minutes=5)
        self.round.save()

        self.client.force_login(self.alice)
        response = self.client.get(reverse('home'))

        self.assertIsNone(response.context['round'])
        self.assertEqual(response.context['results_round'], self.round)
        self.assertContains(response, 'See the results')


@override_settings(WEBPUSH_PUBLIC_KEY='public', WEBPUSH_PRIVATE_KEY='private')
class NotificationTests(QueueUpTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.round.submission_opens = timezone.now() + timedelta(days=3)
        self.round.submission_deadline = timezone.now() + timedelta(days=5)
        self.round.voting_deadline = timezone.now() + timedelta(days=7)
        self.round.reveal_at = timezone.now() + timedelta(days=8)
        self.round.save()
        self.a1 = PushSubscription.objects.create(user=self.alice, endpoint='https://push/a1', p256dh='p', auth='a')
        self.a2 = PushSubscription.objects.create(user=self.alice, endpoint='https://push/a2', p256dh='p', auth='a')
        self.b1 = PushSubscription.objects.create(user=self.bob, endpoint='https://push/b1', p256dh='p', auth='a')

    def test_subscription_is_global_and_future_round_eligible(self):
        self.assertFalse(hasattr(self.a1, 'round'))
        self.assertEqual(PushSubscription.objects.filter(user=self.alice).count(), 2)

    def test_disabling_one_device_does_not_remove_other(self):
        self.client.force_login(self.alice)
        self.client.post(reverse('push_unsubscribe'), data='{"endpoint":"https://push/a1"}', content_type='application/json')
        self.assertFalse(PushSubscription.objects.filter(endpoint='https://push/a1').exists())
        self.assertTrue(PushSubscription.objects.filter(endpoint='https://push/a2').exists())

    @patch('league.push.webpush')
    def test_command_is_idempotent_per_device_event(self, mocked_push):
        call_command('send_round_notifications')
        first = mocked_push.call_count
        call_command('send_round_notifications')
        self.assertEqual(mocked_push.call_count, first)
        self.assertEqual(NotificationDelivery.objects.count(), first)

    @patch('league.push.webpush')
    def test_submitter_not_sent_submission_reminder(self, mocked_push):
        self.round.submission_opens = timezone.now() - timedelta(days=1)
        self.round.submission_deadline = timezone.now() + timedelta(hours=12)
        self.round.save()
        self.submission(self.alice, 'own')
        call_command('send_round_notifications')
        reminder_calls = [call for call in mocked_push.call_args_list if 'submission-reminder' in call.kwargs['data']]
        endpoints = [call.kwargs['subscription_info']['endpoint'] for call in reminder_calls]
        self.assertNotIn('https://push/a1', endpoints)
        self.assertNotIn('https://push/a2', endpoints)

    @patch('league.push.webpush')
    def test_completed_voter_not_sent_voting_reminder(self, mocked_push):
        own = self.submission(self.alice, 'own')
        bob_song = self.submission(self.bob, 'bob')
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=5)
        self.round.submission_deadline = timezone.now() - timedelta(hours=1)
        self.round.voting_deadline = timezone.now() + timedelta(hours=12)
        self.round.save()
        call_command('send_round_notifications')
        reminder_calls = [call for call in mocked_push.call_args_list if 'voting-reminder' in call.kwargs['data']]
        endpoints = [call.kwargs['subscription_info']['endpoint'] for call in reminder_calls]
        self.assertNotIn('https://push/a1', endpoints)
        self.assertNotIn('https://push/a2', endpoints)


class V70MembershipTests(TestCase):
    def test_new_signup_waits_for_approval(self):
        response = self.client.post(reverse('signup'), {
            'display_name': 'New Player', 'username': 'newplayer', 'email': 'new@example.com',
            'password1': 'a-secure-password-123', 'password2': 'a-secure-password-123',
            'agree_to_terms': 'on',
        })
        self.assertRedirects(response, reverse('waiting_approval'))
        user = User.objects.get(username='newplayer')
        self.assertFalse(user.profile.approved)
        self.assertRedirects(self.client.get(reverse('home')), reverse('waiting_approval'))
        self.assertEqual(self.client.get(reverse('notification_settings')).status_code, 200)

    def test_staff_can_approve_user(self):
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        player = User.objects.create_user('pending', password='x')
        self.client.force_login(staff)
        response = self.client.post(reverse('user_action', args=[player.pk, 'approve']))
        self.assertRedirects(response, reverse('control_panel'))
        player.profile.refresh_from_db()
        self.assertTrue(player.profile.approved)
        self.assertIsNotNone(player.profile.approved_at)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordResetTests(TestCase):
    email = 'member@example.com'
    old_password = 'old-password-123'
    new_password = 'new-password-456'

    def setUp(self):
        self.user = User.objects.create_user(
            'member', email=self.email, password=self.old_password,
        )

    def _request_reset(self, email=None):
        return self.client.post(
            reverse('password_reset'), {'email': email or self.email}, follow=True,
        )

    def _reset_url_from_email(self):
        match = re.search(r'https?://[^\s]+/password-reset/[^\s]+', mail.outbox[0].body)
        self.assertIsNotNone(match)
        return match.group(0)

    def test_reset_request_for_existing_user_sends_branded_email(self):
        response = self._request_reset()

        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertContains(response, 'If an account exists for that email')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'Reset your QueueUp password')
        self.assertIn('QueueUp account', mail.outbox[0].body)
        self.assertIn('/password-reset/', mail.outbox[0].body)

    def test_nonexistent_email_gets_same_generic_response(self):
        response = self._request_reset('nobody@example.com')

        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertContains(response, 'If an account exists for that email')
        self.assertEqual(len(mail.outbox), 0)

    def test_valid_reset_token_displays_new_password_form(self):
        self._request_reset()

        response = self.client.get(self._reset_url_from_email(), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choose a new password')
        self.assertContains(response, 'name="new_password1"')
        self.assertContains(response, 'name="new_password2"')

    def test_invalid_reset_token_cannot_change_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))

        response = self.client.get(reverse(
            'password_reset_confirm',
            kwargs={'uidb64': uid, 'token': 'not-a-valid-token'},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid, expired, or has already been used')
        self.assertNotContains(response, 'name="new_password1"')

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_reset_token_cannot_change_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)

        response = self.client.get(
            reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid, expired, or has already been used')
        self.assertNotContains(response, 'name="new_password1"')

    def test_setting_new_password_invalidates_link_and_allows_login(self):
        self._request_reset()
        reset_url = self._reset_url_from_email()
        form_response = self.client.get(reset_url, follow=True)
        set_password_url = form_response.request['PATH_INFO']

        response = self.client.post(set_password_url, {
            'new_password1': self.new_password,
            'new_password2': self.new_password,
        }, follow=True)

        self.assertRedirects(response, reverse('password_reset_complete'))
        self.assertContains(response, 'Password updated')
        self.assertFalse(self.client.login(username='member', password=self.old_password))
        self.assertTrue(self.client.login(username='member', password=self.new_password))

        self.client.logout()
        used_response = self.client.get(reset_url, follow=True)
        self.assertContains(used_response, 'invalid, expired, or has already been used')


class SMTPEmailBackendTests(TestCase):
    @override_settings(
        EMAIL_BACKEND='league.email_backend.EmailBackend',
        EMAIL_CA_FILE='/run/secrets/smtp-ca.crt',
    )
    @patch('django.core.mail.backends.smtp.ssl.create_default_context')
    def test_private_ca_is_added_to_default_verified_context(self, create_context):
        context = create_context.return_value

        connection = get_connection()
        self.assertIs(connection.ssl_context, context)

        context.load_verify_locations.assert_called_once_with(
            cafile='/run/secrets/smtp-ca.crt',
        )


class V70RoundPrivacyTests(QueueUpTestMixin, TestCase):
    def test_hidden_round_is_not_visible_to_players(self):
        self.round.goes_live_at = timezone.now() + timedelta(days=1)
        self.round.save(update_fields=['goes_live_at'])
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('round_detail', args=[self.round.pk])).status_code, 404)

    def test_profile_history_hides_unrevealed_submission(self):
        self.submission(self.alice, 'secret')
        self.client.force_login(self.bob)
        response = self.client.get(reverse('user_stats', args=[self.alice.username]))
        self.assertNotContains(response, 'secret')

    def test_voting_guide_acknowledgement_is_persistent(self):
        self.client.force_login(self.alice)
        self.assertFalse(self.alice.profile.voting_guide_seen)
        self.client.post(reverse('voting_guide_seen'))
        self.alice.profile.refresh_from_db()
        self.assertTrue(self.alice.profile.voting_guide_seen)


class V70CleanMusicTests(QueueUpTestMixin, TestCase):
    @patch('league.views.api_get')
    def test_explicit_track_is_rejected_server_side(self, mocked_get):
        self.round.submission_opens = timezone.now() - timedelta(hours=1)
        self.round.submission_deadline = timezone.now() + timedelta(hours=1)
        self.round.save()
        self.alice.profile.submission_rules_accepted_at = timezone.now()
        self.alice.profile.save(update_fields=['submission_rules_accepted_at'])
        mocked_get.return_value = {
            'id': '1234567890123456789012', 'uri': 'spotify:track:1234567890123456789012',
            'external_urls': {'spotify': 'https://open.spotify.com/track/1234567890123456789012'},
            'name': 'Explicit Track', 'explicit': True, 'artists': [{'id': 'artist', 'name': 'Artist'}],
            'album': {'name': 'Album', 'images': []}, 'preview_url': None,
        }
        self.client.force_login(self.alice)
        response = self.client.post(reverse('submit_song', args=[self.round.pk]), {'track_id': '1234567890123456789012'}, follow=True)
        self.assertContains(response, 'Keep it clean please!')
        self.assertFalse(Submission.objects.filter(round=self.round, user=self.alice).exists())


class SongPickerPreviewTests(QueueUpTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.round.submission_opens = now - timedelta(hours=1)
        self.round.submission_deadline = now + timedelta(hours=1)
        self.round.voting_deadline = now + timedelta(days=2)
        self.round.reveal_at = now + timedelta(days=3)
        self.round.save(update_fields=['submission_opens', 'submission_deadline', 'voting_deadline', 'reveal_at'])

    def test_song_picker_shows_preview_button_and_preview_modal(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse('song_picker', args=[self.round.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-track-preview')
        self.assertContains(response, 'data-track-preview-modal')
        self.assertContains(response, 'data-preview-iframe')
        self.assertContains(response, 'data-song-confirm')
        self.assertContains(response, '>Choose<')


class ProfileUploadCsrfTests(QueueUpTestMixin, TestCase):
    @staticmethod
    def png_bytes():
        image = Image.new('RGB', (1, 1), 'white')
        output = io.BytesIO()
        image.save(output, format='PNG')
        return output.getvalue()

    @staticmethod
    def jpeg_bytes():
        image = Image.new('RGB', (1, 1), 'white')
        output = io.BytesIO()
        image.save(output, format='JPEG')
        return output.getvalue()

    @staticmethod
    def heif_bytes():
        from pillow_heif import register_heif_opener

        register_heif_opener()
        image = Image.new('RGB', (2, 2), 'white')
        output = io.BytesIO()
        image.save(output, format='HEIF', quality=90)
        return output.getvalue()

    def test_profile_edit_has_one_csrf_protected_form_and_separate_upload_form(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse('profile_edit'))
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(content.count('name="csrfmiddlewaretoken"'), 1)
        self.assertContains(response, f'action="{reverse("profile_edit")}"')
        self.assertContains(response, f'action="{reverse("profile_picture_upload")}"')
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertIn('no-cache', response.headers.get('Cache-Control', ''))

    def test_profile_edit_remains_csrf_protected(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(username='alice', password='x'))
        response = client.post(reverse('profile_edit'), {'display_name': 'Blocked Update'})
        self.assertEqual(response.status_code, 403)
        self.alice.refresh_from_db()
        self.assertNotEqual(self.alice.first_name, 'Blocked Update')

    def test_profile_edit_accepts_post_with_csrf_token(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(username='alice', password='x'))
        page = client.get(reverse('profile_edit'))
        token = page.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post(reverse('profile_edit'), {
            'csrfmiddlewaretoken': token,
            'display_name': 'Alice Updated',
        })
        self.assertRedirects(response, reverse('stats'))
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.first_name, 'Alice Updated')

    def test_picture_upload_accepts_multipart_without_csrf_token(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(username='alice', password='x'))
        picture = SimpleUploadedFile('avatar.png', self.png_bytes(), content_type='image/png')
        response = client.post(reverse('profile_picture_upload'), {'picture': picture})
        self.assertRedirects(response, reverse('profile_edit'))
        self.alice.profile.refresh_from_db()
        self.assertTrue(self.alice.profile.picture.name.endswith('.png'))
        self.alice.profile.picture.delete(save=False)

    def test_picture_upload_accepts_raw_image_body_without_csrf_token(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(username='alice', password='x'))
        response = client.generic(
            'POST', reverse('profile_picture_upload'), self.png_bytes(),
            content_type='image/png',
            HTTP_X_QUEUEUP_RAW_UPLOAD='1',
            HTTP_X_QUEUEUP_FILENAME='iphone-screenshot.png',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.alice.profile.refresh_from_db()
        self.assertTrue(self.alice.profile.picture.name.endswith('.png'))
        self.alice.profile.picture.delete(save=False)

    def test_raw_jpeg_upload_normalizes_jpeg_extension_and_bad_mime_type(self):
        self.client.force_login(self.alice)
        response = self.client.generic(
            'POST', reverse('profile_picture_upload'), self.jpeg_bytes(),
            content_type='application/octet-stream',
            HTTP_X_QUEUEUP_RAW_UPLOAD='1',
            HTTP_X_QUEUEUP_FILENAME='iphone-converted-photo.jpeg',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.alice.profile.refresh_from_db()
        self.assertTrue(self.alice.profile.picture.name.endswith('.jpg'))
        self.alice.profile.picture.delete(save=False)

    def test_multipart_heif_named_jpeg_is_converted_to_real_jpeg(self):
        self.client.force_login(self.alice)
        picture = SimpleUploadedFile(
            'IMG_5678.jpeg', self.heif_bytes(), content_type='image/heic'
        )
        response = self.client.post(reverse('profile_picture_upload'), {'picture': picture})
        self.assertRedirects(response, reverse('profile_edit'))
        self.alice.profile.refresh_from_db()
        self.assertTrue(self.alice.profile.picture.name.endswith('.jpg'))
        with Image.open(self.alice.profile.picture) as saved:
            self.assertEqual(saved.format, 'JPEG')
        self.alice.profile.picture.delete(save=False)

    def test_raw_heif_named_jpeg_is_converted_to_real_jpeg(self):
        self.client.force_login(self.alice)
        response = self.client.generic(
            'POST', reverse('profile_picture_upload'), self.heif_bytes(),
            content_type='image/heic',
            HTTP_X_QUEUEUP_RAW_UPLOAD='1',
            HTTP_X_QUEUEUP_FILENAME='IMG_1234.jpeg',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.alice.profile.refresh_from_db()
        self.assertTrue(self.alice.profile.picture.name.endswith('.jpg'))
        with Image.open(self.alice.profile.picture) as saved:
            self.assertEqual(saved.format, 'JPEG')
        self.alice.profile.picture.delete(save=False)

    def test_raw_picture_upload_rejects_empty_body(self):
        self.client.force_login(self.alice)
        response = self.client.generic(
            'POST', reverse('profile_picture_upload'), b'',
            content_type='image/png',
            HTTP_X_QUEUEUP_RAW_UPLOAD='1',
            HTTP_X_QUEUEUP_FILENAME='empty.png',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])

    def test_picture_upload_requires_login_and_post(self):
        response = self.client.post(reverse('profile_picture_upload'))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("profile_picture_upload")}',
            fetch_redirect_response=False,
        )
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('profile_picture_upload')).status_code, 405)

    def test_picture_upload_rejects_missing_invalid_and_oversized_images(self):
        self.client.force_login(self.alice)

        response = self.client.post(reverse('profile_picture_upload'), {})
        self.assertRedirects(response, reverse('profile_edit'))
        self.assertFalse(bool(self.alice.profile.picture))

        invalid = SimpleUploadedFile('avatar.txt', b'not an image', content_type='text/plain')
        response = self.client.post(reverse('profile_picture_upload'), {'picture': invalid})
        self.assertRedirects(response, reverse('profile_edit'))
        self.alice.profile.refresh_from_db()
        self.assertFalse(bool(self.alice.profile.picture))

        oversized = SimpleUploadedFile(
            'large.png', self.png_bytes() + (b'\x00' * (5 * 1024 * 1024)), content_type='image/png'
        )
        response = self.client.post(reverse('profile_picture_upload'), {'picture': oversized})
        self.assertRedirects(response, reverse('profile_edit'))
        self.alice.profile.refresh_from_db()
        self.assertFalse(bool(self.alice.profile.picture))


    def test_remove_picture_is_in_picture_card_and_remains_csrf_protected(self):
        from django.test import Client
        picture = SimpleUploadedFile('avatar.png', self.png_bytes(), content_type='image/png')
        self.alice.profile.picture = picture
        self.alice.profile.save(update_fields=['picture', 'updated_at'])

        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(username='alice', password='x'))
        page = client.get(reverse('profile_edit'))
        self.assertContains(page, f'action="{reverse("profile_picture_remove")}"')
        self.assertEqual(page.content.decode().count('name="csrfmiddlewaretoken"'), 2)

        blocked = client.post(reverse('profile_picture_remove'))
        self.assertEqual(blocked.status_code, 403)
        self.alice.profile.refresh_from_db()
        self.assertTrue(bool(self.alice.profile.picture))

        token = page.cookies[settings.CSRF_COOKIE_NAME].value
        removed = client.post(reverse('profile_picture_remove'), {'csrfmiddlewaretoken': token})
        self.assertRedirects(removed, reverse('profile_edit'))
        self.alice.profile.refresh_from_db()
        self.assertFalse(bool(self.alice.profile.picture))

class RoundStatusTests(QueueUpTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user('staff', password='x', is_staff=True)

    def test_round_status_requires_staff(self):
        url = reverse('round_status', args=[self.round.pk])
        self.client.force_login(self.alice)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_round_status_shows_submissions_and_voting_completion(self):
        alice_song = self.submission(self.alice, 'Alice Song')
        bob_song = self.submission(self.bob, 'Bob Song')
        cara_song = self.submission(self.cara, 'Cara Song')

        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=4)
        Vote.objects.create(round=self.round, voter=self.alice, submission=cara_song, score=5)
        Vote.objects.create(round=self.round, voter=self.bob, submission=alice_song, score=3)

        self.client.force_login(self.staff)
        response = self.client.get(reverse('round_status', args=[self.round.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Alice Song')
        self.assertContains(response, 'Bob Song')
        self.assertContains(response, 'Cara Song')
        self.assertContains(response, 'Complete')
        self.assertContains(response, 'In progress')
        self.assertContains(response, 'Not started')
        self.assertContains(response, 'No submission')

        rows = {row['player'].username: row for row in response.context['rows']}
        self.assertTrue(rows['alice']['voting_complete'])
        self.assertEqual(rows['alice']['voted_count'], 2)
        self.assertEqual(rows['alice']['eligible_count'], 2)
        self.assertFalse(rows['bob']['voting_complete'])
        self.assertEqual(rows['bob']['voted_count'], 1)
        self.assertEqual(rows['bob']['eligible_count'], 2)
        self.assertFalse(rows['cara']['voting_started'])
        self.assertEqual(response.context['submitted_count'], 3)
        self.assertEqual(response.context['completed_count'], 1)

    def test_control_panel_links_each_round_to_status_page(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('control_panel'))
        self.assertContains(response, reverse('round_status', args=[self.round.pk]))
        self.assertContains(response, 'Round stats')

class GenreHopperAchievementTests(QueueUpTestMixin, TestCase):
    def _revealed_round(self, number):
        now = timezone.now()
        return Round.objects.create(
            season=self.season,
            prompt=f'Genre round {number}',
            submission_opens=now - timezone.timedelta(days=5),
            submission_deadline=now - timezone.timedelta(days=4),
            voting_deadline=now - timezone.timedelta(days=3),
            reveal_at=now - timezone.timedelta(days=2),
        )

    def test_one_song_with_many_subgenres_counts_as_only_one_main_genre(self):
        from .achievements import achievement_checks
        from .models import Submission

        rnd = self._revealed_round(1)
        Submission.objects.create(
            round=rnd, user=self.alice, spotify_track_id='genre-one',
            spotify_uri='spotify:track:genre-one', spotify_url='https://open.spotify.com/track/genre-one',
            title='One Song', artist='One Artist',
            genres=['christian hip hop', 'gospel rap', 'worship', 'trap', 'pop rap', 'rap'],
        )

        self.assertFalse(achievement_checks(self.alice)['genre_hopper'])

    def test_unrevealed_round_does_not_count_toward_genre_hopper(self):
        from .achievements import achievement_checks
        from .models import Submission

        broad_genres = [
            'rock', 'pop', 'jazz', 'country', 'hip hop',
            'electronic', 'classical', 'reggae', 'folk',
        ]
        for index, genre in enumerate(broad_genres, start=1):
            rnd = self._revealed_round(index)
            Submission.objects.create(
                round=rnd, user=self.alice, spotify_track_id=f'revealed-{index}',
                spotify_uri=f'spotify:track:revealed-{index}',
                spotify_url=f'https://open.spotify.com/track/revealed-{index}',
                title=f'Song {index}', artist=f'Artist {index}', genres=[genre],
            )

        Submission.objects.create(
            round=self.round, user=self.alice, spotify_track_id='unfinished-genre',
            spotify_uri='spotify:track:unfinished-genre',
            spotify_url='https://open.spotify.com/track/unfinished-genre',
            title='Unfinished Song', artist='Unfinished Artist', genres=['blues'],
        )

        self.assertFalse(achievement_checks(self.alice)['genre_hopper'])

    def test_ten_completed_songs_in_ten_main_genres_unlocks(self):
        from .achievements import achievement_checks
        from .models import Submission

        genre_tags = [
            ['alternative rock', 'indie rock'],
            ['dance pop', 'electropop'],
            ['vocal jazz', 'bebop'],
            ['modern country', 'americana'],
            ['christian hip hop', 'gospel rap'],
            ['progressive house', 'edm'],
            ['romantic classical', 'orchestra'],
            ['roots reggae', 'dancehall'],
            ['indie folk', 'singer-songwriter'],
            ["children's music", 'nursery'],
        ]
        for index, genres in enumerate(genre_tags, start=1):
            rnd = self._revealed_round(index)
            Submission.objects.create(
                round=rnd, user=self.alice, spotify_track_id=f'genre-{index}',
                spotify_uri=f'spotify:track:genre-{index}',
                spotify_url=f'https://open.spotify.com/track/genre-{index}',
                title=f'Genre Song {index}', artist=f'Genre Artist {index}', genres=genres,
            )

        self.assertTrue(achievement_checks(self.alice)['genre_hopper'])

class IsrcDuplicateSubmissionTests(QueueUpTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.round.submission_opens = now - timedelta(hours=1)
        self.round.submission_deadline = now + timedelta(hours=1)
        self.round.save(update_fields=['submission_opens', 'submission_deadline'])
        self.alice.profile.submission_rules_accepted_at = now
        self.alice.profile.save(update_fields=['submission_rules_accepted_at'])
        self.bob.profile.submission_rules_accepted_at = now
        self.bob.profile.save(update_fields=['submission_rules_accepted_at'])

    @staticmethod
    def spotify_track(track_id, name, isrc):
        return {
            'id': track_id,
            'uri': f'spotify:track:{track_id}',
            'external_urls': {'spotify': f'https://open.spotify.com/track/{track_id}'},
            'external_ids': {'isrc': isrc},
            'name': name,
            'explicit': False,
            'artists': [{'id': 'artist-id', 'name': 'Artist'}],
            'album': {'name': 'Album', 'images': []},
            'preview_url': None,
        }

    @patch('league.views.api_get')
    def test_search_grays_out_different_track_id_with_same_isrc(self, mocked_get):
        Submission.objects.create(
            round=self.round, user=self.alice,
            spotify_track_id='AAAAAAAAAAAAAAAAAAAAAA',
            isrc='USABC1234567',
            spotify_uri='spotify:track:AAAAAAAAAAAAAAAAAAAAAA',
            spotify_url='https://open.spotify.com/track/AAAAAAAAAAAAAAAAAAAAAA',
            title='Same Recording', artist='Artist',
        )
        mocked_get.return_value = {
            'tracks': {'items': [self.spotify_track('BBBBBBBBBBBBBBBBBBBBBB', 'Same Recording', 'usabc1234567')]}
        }
        self.client.force_login(self.bob)
        response = self.client.get(reverse('spotify_search'), {'q': 'same recording', 'round': self.round.pk})
        self.assertEqual(response.status_code, 200)
        track = response.json()['tracks'][0]
        self.assertEqual(track['isrc'], 'USABC1234567')
        self.assertTrue(track['used'])

    @patch('league.views.genres_for_artists', return_value=[])
    @patch('league.views.api_get')
    def test_server_rejects_same_isrc_with_different_track_id(self, mocked_get, mocked_genres):
        Submission.objects.create(
            round=self.round, user=self.alice,
            spotify_track_id='AAAAAAAAAAAAAAAAAAAAAA',
            isrc='USABC1234567',
            spotify_uri='spotify:track:AAAAAAAAAAAAAAAAAAAAAA',
            spotify_url='https://open.spotify.com/track/AAAAAAAAAAAAAAAAAAAAAA',
            title='Same Recording', artist='Artist',
        )
        mocked_get.return_value = self.spotify_track('BBBBBBBBBBBBBBBBBBBBBB', 'Same Recording', 'USABC1234567')
        self.client.force_login(self.bob)
        response = self.client.post(
            reverse('submit_song', args=[self.round.pk]),
            {'track_id': 'BBBBBBBBBBBBBBBBBBBBBB'},
            follow=True,
        )
        self.assertContains(response, 'That recording has already been submitted for this round.')
        self.assertFalse(Submission.objects.filter(round=self.round, user=self.bob).exists())

    @patch('league.views.genres_for_artists', return_value=[])
    @patch('league.views.api_get')
    def test_new_submission_saves_normalized_isrc(self, mocked_get, mocked_genres):
        mocked_get.return_value = self.spotify_track('CCCCCCCCCCCCCCCCCCCCCC', 'New Recording', 'usxyz7654321')
        self.client.force_login(self.alice)
        self.client.post(reverse('submit_song', args=[self.round.pk]), {'track_id': 'CCCCCCCCCCCCCCCCCCCCCC'})
        submission = Submission.objects.get(round=self.round, user=self.alice)
        self.assertEqual(submission.isrc, 'USXYZ7654321')

class AdminManagementAndSubmissionBonusTests(QueueUpTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user('staff-admin', password='x', is_staff=True)
        self.client.force_login(self.staff)

    def _reveal_round(self):
        now = timezone.now()
        self.round.submission_opens = now - timedelta(days=4)
        self.round.submission_deadline = now - timedelta(days=3)
        self.round.voting_deadline = now - timedelta(days=2)
        self.round.reveal_at = now - timedelta(days=1)
        self.round.save(update_fields=[
            'submission_opens', 'submission_deadline', 'voting_deadline', 'reveal_at'
        ])

    def test_round_counts_are_not_multiplied_by_join(self):
        first = Submission.objects.create(
            round=self.round, user=self.alice, spotify_track_id='count-a',
            spotify_uri='spotify:track:count-a', spotify_url='https://open.spotify.com/track/count-a',
            title='First', artist='Artist',
        )
        second = Submission.objects.create(
            round=self.round, user=self.bob, spotify_track_id='count-b',
            spotify_uri='spotify:track:count-b', spotify_url='https://open.spotify.com/track/count-b',
            title='Second', artist='Artist',
        )
        Vote.objects.create(round=self.round, voter=self.bob, submission=first, score=4)
        Vote.objects.create(round=self.round, voter=self.alice, submission=second, score=5)
        Vote.objects.create(round=self.round, voter=self.cara, submission=first, score=3)

        response = self.client.get(reverse('control_rounds'))
        row = next(r for r in response.context['rounds'] if r.pk == self.round.pk)
        self.assertEqual(row.submission_count, 2)
        self.assertEqual(row.vote_count, 3)

    def test_round_cards_show_submission_and_completed_voter_progress(self):
        alice_song = Submission.objects.create(
            round=self.round, user=self.alice, spotify_track_id='progress-a',
            spotify_uri='spotify:track:progress-a', spotify_url='https://open.spotify.com/track/progress-a',
            title='Alice Song', artist='Artist',
        )
        bob_song = Submission.objects.create(
            round=self.round, user=self.bob, spotify_track_id='progress-b',
            spotify_uri='spotify:track:progress-b', spotify_url='https://open.spotify.com/track/progress-b',
            title='Bob Song', artist='Artist',
        )
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=4)
        Vote.objects.create(round=self.round, voter=self.bob, submission=alice_song, score=5)
        Vote.objects.create(round=self.round, voter=self.cara, submission=alice_song, score=3)
        Vote.objects.create(round=self.round, voter=self.staff, submission=alice_song, score=4)
        Vote.objects.create(round=self.round, voter=self.staff, submission=bob_song, score=4)

        response = self.client.get(reverse('control_rounds'))
        row = next(r for r in response.context['rounds'] if r.pk == self.round.pk)
        self.assertEqual(row.submitted_player_count, 2)
        self.assertEqual(row.league_player_count, 4)
        self.assertEqual(row.completed_voter_count, 3)
        self.assertContains(response, '<b>2/4</b> people submitted', html=True)
        self.assertContains(response, '<b>3/4</b> people voted', html=True)

    def test_admin_sections_are_separate_and_searchable(self):
        response = self.client.get(reverse('control_panel'))
        self.assertContains(response, reverse('control_rounds'))
        self.assertContains(response, reverse('control_badges'))
        self.assertContains(response, reverse('control_users'))

        round_response = self.client.get(reverse('control_rounds'), {'q': self.round.prompt})
        self.assertContains(round_response, self.round.prompt)
        no_round_response = self.client.get(reverse('control_rounds'), {'q': 'not-a-real-prompt'})
        self.assertNotContains(no_round_response, self.round.prompt)

        user_response = self.client.get(reverse('control_users'), {'q': self.alice.username})
        self.assertContains(user_response, self.alice.username)
        self.assertNotContains(user_response, self.bob.username)

    def test_each_submission_adds_four_points_immediately(self):
        Submission.objects.create(
            round=self.round, user=self.alice, spotify_track_id='bonus-a',
            spotify_uri='spotify:track:bonus-a', spotify_url='https://open.spotify.com/track/bonus-a',
            title='Bonus Song', artist='Artist',
        )
        response = self.client.get(reverse('leaderboard'))
        alice = next(entry.item for entry in response.context['players'] if entry.item.pk == self.alice.pk)
        self.assertEqual(alice.submission_bonus, 4)
        self.assertEqual(alice.total_score, 4)
