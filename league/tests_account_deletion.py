from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AnonymousParticipant, Round, Season, Submission, SpotifyConnection, Vote
from .services.account_deletion import delete_account
from .voting import counted_vote_ids_for_round


class AccountDeletionTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.season = Season.objects.create(
            name='Deletion season', starts_at=now - timedelta(days=10),
            ends_at=now + timedelta(days=10),
        )
        self.round = Round.objects.create(
            season=self.season, prompt='A prompt', submission_opens=now - timedelta(days=3),
            submission_deadline=now - timedelta(days=2), voting_deadline=now - timedelta(days=1),
            reveal_at=now - timedelta(hours=1),
        )
        self.alice = User.objects.create_user(
            'alice-delete', password='correct horse battery staple',
            email='alice@example.com', first_name='Alice',
        )
        self.alice.profile.approved = True
        self.alice.profile.save(update_fields=['approved'])
        self.bob = User.objects.create_user('bob-delete', password='password')
        self.bob.profile.approved = True
        self.bob.profile.save(update_fields=['approved'])
        self.cara = User.objects.create_user('cara-delete', password='password')
        self.cara.profile.approved = True
        self.cara.profile.save(update_fields=['approved'])

    def song(self, user, track):
        return Submission.objects.create(
            round=self.round, user=user, spotify_track_id=track,
            spotify_uri=f'spotify:track:{track}', spotify_url=f'https://spotify.test/{track}',
            title=track, artist='Artist',
        )

    def test_unauthenticated_page_redirects_directly_to_login(self):
        response = self.client.get(reverse('account_delete'))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("account_delete")}',
        )

    def test_pending_user_can_reach_deletion_page(self):
        self.alice.profile.approved = False
        self.alice.profile.save(update_fields=['approved'])
        self.client.force_login(self.alice)
        response = self.client.get(reverse('account_delete'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Delete your QueueUp account')

    def test_requires_username_password_and_delete_phrase(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse('account_delete'), {
            'username': self.alice.username,
            'password': 'wrong',
            'confirmation': 'delete',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(pk=self.alice.pk).exists())
        self.assertContains(response, 'The password is incorrect.')

    def test_deletion_removes_personal_records_and_invalidates_session(self):
        connection = SpotifyConnection.objects.create(
            user=self.alice, access_token='access', refresh_token='refresh',
            expires_at=timezone.now(),
        )
        self.client.force_login(self.alice)
        response = self.client.post(reverse('account_delete'), {
            'username': self.alice.username,
            'password': 'correct horse battery staple',
            'confirmation': 'DELETE',
        })
        self.assertRedirects(response, reverse('account_deleted'))
        self.assertFalse(User.objects.filter(pk=self.alice.pk).exists())
        self.assertFalse(SpotifyConnection.objects.filter(pk=connection.pk).exists())
        self.assertFalse(self.client.session.get('_auth_user_id'))
        self.assertEqual(AnonymousParticipant.objects.count(), 1)
        self.assertContains(self.client.get(reverse('account_deleted')), 'Your account has been deleted.')

    def test_completed_deleted_ballot_stays_counted_as_separate_anonymous_identity(self):
        own = self.song(self.alice, 'alice-song')
        bob_song = self.song(self.bob, 'bob-song')
        cara_song = self.song(self.cara, 'cara-song')
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=4)
        Vote.objects.create(round=self.round, voter=self.alice, submission=cara_song, score=5)
        delete_account(self.alice)
        own.refresh_from_db()
        votes = list(Vote.objects.filter(anonymous_participant=own.anonymous_participant))
        self.assertIsNone(own.user)
        self.assertEqual(len(votes), 2)
        self.assertEqual(len(counted_vote_ids_for_round(self.round)), 2)

    def test_incomplete_deleted_ballot_does_not_become_counted(self):
        self.song(self.alice, 'alice-song')
        bob_song = self.song(self.bob, 'bob-song')
        self.song(self.cara, 'cara-song')
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=4)
        delete_account(self.alice)
        self.assertEqual(counted_vote_ids_for_round(self.round), set())

    def test_deleted_history_api_uses_anonymous_display_name(self):
        song = self.song(self.alice, 'alice-song')
        delete_account(self.alice)
        self.client.force_login(self.bob)
        response = self.client.get(reverse('api-v1:results', args=[self.round.pk]))
        self.assertEqual(response.status_code, 200)
        result = response.json()['data']['results'][0]
        self.assertEqual(result['submitter']['username'], 'deleted-user')
        self.assertIsNone(result['submitter']['id'])
        self.assertNotIn('alice-delete', response.content.decode())
