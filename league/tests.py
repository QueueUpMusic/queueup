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

from .models import AchievementUnlock, Badge, NotificationDelivery, PushSubscription, Round, Season, SeasonWelcome, Submission, UserBadge, Vote
from .ranking import competition_rank, ranked_submissions, winner_ids
from .services.ballots import ballot_for_user
from .services.profiles import profile_for_user, profile_metrics as service_profile_metrics
from .services.notifications import (
    achievement_notification_events,
    round_notification_events,
    submission_reminder_audience,
    voting_reminder_audience,
)
from .services.scoring import SUBMISSION_BONUS_POINTS, season_leaderboard
from .services import votes as vote_service
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
    @patch('league.views.broadcast')
    def test_first_rating_creates_one_vote_and_preserves_realtime_event(
        self, mocked_broadcast,
    ):
        self.submission(self.alice, 'own-first-vote')
        bob_song = self.submission(self.bob, 'bob-first-vote')
        self.client.force_login(self.alice)

        response = self.client.post(
            reverse('vote', args=[self.round.pk, bob_song.pk]),
            {'score': 4},
        )

        self.assertRedirects(
            response,
            reverse('round_detail', args=[self.round.pk]),
            fetch_redirect_response=False,
        )
        votes = Vote.objects.filter(voter=self.alice, submission=bob_song)
        self.assertEqual(votes.count(), 1)
        self.assertEqual(votes.get().score, 4)
        mocked_broadcast.assert_called_once_with(
            'vote_saved',
            round_id=self.round.id,
            votes=1,
        )

    def test_ballot_excludes_voters_own_submission(self):
        own = self.submission(self.alice, 'own-ineligible')
        bob_song = self.submission(self.bob, 'bob-eligible')
        cara_song = self.submission(self.cara, 'cara-eligible')

        ballot = ballot_for_user(self.round, self.alice)

        self.assertEqual(ballot.eligible_ids, {bob_song.id, cara_song.id})
        self.assertNotIn(own.id, ballot.eligible_ids)
        self.assertEqual(ballot.eligible_count, 2)

    def test_vote_endpoint_rejects_voters_own_submission(self):
        own = self.submission(self.alice, 'own-blocked')
        self.client.force_login(self.alice)

        response = self.client.post(
            reverse('vote', args=[self.round.pk, own.pk]),
            {'score': 5},
            follow=True,
        )

        self.assertContains(response, 'vote for your own song')
        self.assertFalse(Vote.objects.filter(voter=self.alice, submission=own).exists())

    def test_ballot_progress_tracks_saved_votes_and_completion(self):
        self.submission(self.alice, 'own-progress')
        bob_song = self.submission(self.bob, 'bob-progress')
        cara_song = self.submission(self.cara, 'cara-progress')
        Vote.objects.create(
            round=self.round, voter=self.alice, submission=bob_song, score=3,
        )

        partial = ballot_for_user(self.round, self.alice)

        self.assertEqual(partial.voted_ids, {bob_song.id})
        self.assertEqual(partial.voted_count, 1)
        self.assertEqual(partial.eligible_count, 2)
        self.assertFalse(partial.complete)

        Vote.objects.create(
            round=self.round, voter=self.alice, submission=cara_song, score=4,
        )

        complete = ballot_for_user(self.round, self.alice)
        self.assertEqual(complete.voted_ids, {bob_song.id, cara_song.id})
        self.assertEqual(complete.voted_count, 2)
        self.assertTrue(complete.complete)

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

    def test_ajax_progress_increments_and_final_vote_completes_ballot(self):
        self.submission(self.alice, 'own-progress-ajax')
        bob_song = self.submission(self.bob, 'bob-progress-ajax')
        cara_song = self.submission(self.cara, 'cara-progress-ajax')
        self.client.force_login(self.alice)

        partial = self.client.post(
            reverse('vote', args=[self.round.pk, bob_song.pk]),
            {'score': 3},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(partial.status_code, 200)
        self.assertEqual(partial.json()['voted_count'], 1)
        self.assertEqual(partial.json()['eligible_count'], 2)
        self.assertFalse(partial.json()['complete'])

        complete = self.client.post(
            reverse('vote', args=[self.round.pk, cara_song.pk]),
            {'score': 5},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json()['voted_count'], 2)
        self.assertEqual(complete.json()['eligible_count'], 2)
        self.assertTrue(complete.json()['complete'])
        revisit = self.client.get(reverse('round_detail', args=[self.round.pk]))
        self.assertTrue(revisit.context['voting_complete'])
        self.assertContains(revisit, "You're all caught up!")

    def test_review_page_loads_previously_saved_scores(self):
        self.submission(self.alice, 'own-review')
        bob_song = self.submission(self.bob, 'bob-review')
        cara_song = self.submission(self.cara, 'cara-review')
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=2)
        Vote.objects.create(round=self.round, voter=self.alice, submission=cara_song, score=5)

        self.client.force_login(self.alice)
        response = self.client.get(reverse('round_detail', args=[self.round.pk]) + '?review=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['vote_scores'],
            {bob_song.id: 2, cara_song.id: 5},
        )
        self.assertContains(response, 'name="score" value="2"', count=1)
        self.assertContains(response, 'name="score" value="5"', count=1)

    def test_editing_one_vote_does_not_change_other_saved_votes(self):
        self.submission(self.alice, 'own-edit')
        bob_song = self.submission(self.bob, 'bob-edit')
        cara_song = self.submission(self.cara, 'cara-edit')
        bob_vote = Vote.objects.create(
            round=self.round, voter=self.alice, submission=bob_song, score=2,
        )
        Vote.objects.create(round=self.round, voter=self.alice, submission=cara_song, score=5)

        self.client.force_login(self.alice)
        response = self.client.post(
            reverse('vote', args=[self.round.pk, bob_song.pk]),
            {'score': 4},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        updated = Vote.objects.get(voter=self.alice, submission=bob_song)
        self.assertEqual(updated.pk, bob_vote.pk)
        self.assertEqual(updated.score, 4)
        self.assertEqual(Vote.objects.get(voter=self.alice, submission=cara_song).score, 5)
        self.assertEqual(Vote.objects.filter(voter=self.alice).count(), 2)

    @patch('league.views.broadcast')
    def test_invalid_score_is_rejected_without_mutation_or_broadcast(
        self, mocked_broadcast,
    ):
        bob_song = self.submission(self.bob, 'bob-invalid-score')
        self.client.force_login(self.alice)

        response = self.client.post(
            reverse('vote', args=[self.round.pk, bob_song.pk]),
            {'score': 6},
        )

        self.assertRedirects(
            response,
            reverse('round_detail', args=[self.round.pk]),
            fetch_redirect_response=False,
        )
        self.assertFalse(
            Vote.objects.filter(voter=self.alice, submission=bob_song).exists()
        )
        mocked_broadcast.assert_not_called()
        with self.assertRaises(vote_service.InvalidVoteScore):
            vote_service.record_vote(self.round, self.alice, bob_song, 6)

    @patch('league.views.broadcast')
    def test_voting_outside_permitted_phase_is_rejected(
        self, mocked_broadcast,
    ):
        bob_song = self.submission(self.bob, 'bob-closed-voting')
        now = timezone.now()
        self.round.voting_deadline = now - timedelta(hours=2)
        self.round.reveal_at = now - timedelta(hours=1)
        self.round.save(update_fields=['voting_deadline', 'reveal_at'])
        self.client.force_login(self.alice)

        response = self.client.post(
            reverse('vote', args=[self.round.pk, bob_song.pk]),
            {'score': 4},
            follow=True,
        )

        self.assertContains(response, 'Voting is not open.')
        self.assertFalse(
            Vote.objects.filter(voter=self.alice, submission=bob_song).exists()
        )
        mocked_broadcast.assert_not_called()

    def test_changed_rating_keeps_completion(self):
        self.submission(self.alice, 'own')
        bob_song = self.submission(self.bob, 'bob')
        Vote.objects.create(round=self.round, voter=self.alice, submission=bob_song, score=2)
        self.client.force_login(self.alice)
        response = self.client.post(reverse('vote', args=[self.round.pk, bob_song.pk]), {'score': 4}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(response.json()['complete'])
        self.assertEqual(Vote.objects.get(voter=self.alice, submission=bob_song).score, 4)
        review = self.client.get(
            reverse('round_detail', args=[self.round.pk]) + '?review=1'
        )
        self.assertEqual(review.context['vote_scores'][bob_song.id], 4)
        self.assertContains(review, 'name="score" value="4"', count=1)


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


class ProfileReadServiceTests(QueueUpTestMixin, TestCase):
    def test_profile_scores_exclude_an_incomplete_ballot_after_close(self):
        alice_song = self.submission(self.alice, 'profile-alice')
        bob_song = self.submission(self.bob, 'profile-bob')
        self.submission(self.cara, 'profile-cara')

        # Bob's single rating is an incomplete ballot and must not contribute.
        Vote.objects.create(
            round=self.round,
            voter=self.bob,
            submission=alice_song,
            score=1,
        )
        # Cara completes both ratings she is eligible to cast.
        Vote.objects.create(
            round=self.round,
            voter=self.cara,
            submission=alice_song,
            score=5,
        )
        Vote.objects.create(
            round=self.round,
            voter=self.cara,
            submission=bob_song,
            score=4,
        )
        now = timezone.now()
        self.round.voting_deadline = now - timedelta(hours=2)
        self.round.reveal_at = now - timedelta(hours=1)
        self.round.save(update_fields=['voting_deadline', 'reveal_at'])

        metrics = service_profile_metrics(self.alice, now=now)
        submission = metrics.revealed.get(pk=alice_song.pk)
        profile = profile_for_user(self.alice, now=now)

        self.assertEqual(submission.vote_count, 1)
        self.assertEqual(submission.avg, 5)
        self.assertEqual(profile.avg_received, 5)
        self.assertEqual(profile.round_count, 1)
        self.assertEqual(list(profile.seasons), [self.season])

        self.client.force_login(self.alice)
        response = self.client.get(reverse('stats'))
        self.assertEqual(response.context['avg_received'], 5)
        self.assertContains(response, 'profile-alice')

    def test_legacy_profile_metrics_contract_remains_available(self):
        from .achievements import profile_metrics

        metrics = profile_metrics(self.alice)

        self.assertEqual(
            set(metrics),
            {
                'submissions', 'revealed', 'wins', 'podiums', 'placements',
                'ties_for_first', 'counted_vote_ids',
            },
        )


class SeasonLeaderboardScoringTests(QueueUpTestMixin, TestCase):
    def leaderboard_entry(self, user):
        return next(
            entry
            for entry in season_leaderboard(self.season)
            if entry.item.pk == user.pk
        )

    def create_revealed_round(self, prompt):
        now = timezone.now()
        return Round.objects.create(
            season=self.season,
            prompt=prompt,
            submission_opens=now - timedelta(days=4),
            submission_deadline=now - timedelta(days=3),
            voting_deadline=now - timedelta(days=2),
            reveal_at=now - timedelta(days=1),
        )

    @staticmethod
    def create_submission(round_obj, user, track):
        return Submission.objects.create(
            round=round_obj,
            user=user,
            spotify_track_id=track,
            spotify_uri=f'spotify:track:{track}',
            spotify_url=f'https://open.spotify.com/track/{track}',
            title=track,
            artist='Artist',
        )

    def reveal_current_round(self):
        now = timezone.now()
        self.round.voting_deadline = now - timedelta(hours=2)
        self.round.reveal_at = now - timedelta(hours=1)
        self.round.save(update_fields=['voting_deadline', 'reveal_at'])

    def test_submission_bonus_is_added_immediately(self):
        self.submission(self.alice, 'bonus-now')

        player = self.leaderboard_entry(self.alice).item

        self.assertEqual(SUBMISSION_BONUS_POINTS, 4)
        self.assertEqual(player.submission_bonus, 4)
        self.assertEqual(player.total_score, 4)

    def test_deleting_submission_removes_its_bonus(self):
        first = self.submission(self.alice, 'bonus-first')
        other_round = self.create_revealed_round('Other round')
        second = self.create_submission(
            other_round,
            self.alice,
            'bonus-second',
        )
        self.assertEqual(
            self.leaderboard_entry(self.alice).item.submission_bonus,
            8,
        )

        second.delete()

        player = self.leaderboard_entry(self.alice).item
        self.assertEqual(player.submission_bonus, 4)
        self.assertEqual(player.rounds_played, 1)
        self.assertTrue(Submission.objects.filter(pk=first.pk).exists())

    def test_replacing_submission_restores_bonus(self):
        original = self.submission(self.alice, 'bonus-original')
        original.delete()
        self.assertFalse(any(
            entry.item.pk == self.alice.pk
            for entry in season_leaderboard(self.season)
        ))

        self.submission(self.alice, 'bonus-replacement')

        player = self.leaderboard_entry(self.alice).item
        self.assertEqual(player.submission_bonus, 4)
        self.assertEqual(player.total_score, 4)

    def test_incomplete_ballot_is_excluded_from_leaderboard(self):
        alice_song = self.submission(self.alice, 'score-alice')
        bob_song = self.submission(self.bob, 'score-bob')
        cara_song = self.submission(self.cara, 'score-cara')
        Vote.objects.create(
            round=self.round,
            voter=self.alice,
            submission=bob_song,
            score=4,
        )
        Vote.objects.create(
            round=self.round,
            voter=self.alice,
            submission=cara_song,
            score=4,
        )
        Vote.objects.create(
            round=self.round,
            voter=self.bob,
            submission=alice_song,
            score=5,
        )
        self.reveal_current_round()

        players = {
            entry.item.pk: entry.item
            for entry in season_leaderboard(self.season)
        }

        self.assertIsNone(players[self.alice.pk].vote_score)
        self.assertEqual(players[self.alice.pk].total_score, 4)
        self.assertEqual(players[self.bob.pk].total_score, 8)
        self.assertEqual(players[self.cara.pk].total_score, 8)

    def test_equal_scores_receive_tied_competition_rank(self):
        alice_song = self.submission(self.alice, 'tie-alice')
        bob_song = self.submission(self.bob, 'tie-bob')
        Vote.objects.create(
            round=self.round,
            voter=self.cara,
            submission=alice_song,
            score=5,
        )
        Vote.objects.create(
            round=self.round,
            voter=self.cara,
            submission=bob_song,
            score=5,
        )
        self.reveal_current_round()

        leaderboard = season_leaderboard(self.season)

        self.assertEqual([entry.place for entry in leaderboard], [1, 1])
        self.assertTrue(all(entry.tied for entry in leaderboard))
        self.assertEqual([entry.item.total_score for entry in leaderboard], [9, 9])

    def test_archived_round_remains_in_leaderboard(self):
        alice_song = self.submission(self.alice, 'archived-alice')
        Vote.objects.create(
            round=self.round,
            voter=self.bob,
            submission=alice_song,
            score=5,
        )
        self.reveal_current_round()
        self.round.archived = True
        self.round.save(update_fields=['archived'])

        player = self.leaderboard_entry(self.alice).item

        self.assertEqual(player.vote_score, 5)
        self.assertEqual(player.submission_bonus, 4)
        self.assertEqual(player.total_score, 9)


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
        self.assertFalse(response.context['current_first'])
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
        self.assertEqual(list(archive_response.context['rounds']), [self.round])

    def test_newest_active_round_is_selected(self):
        now = timezone.now()
        self.round.prompt = 'Older active prompt'
        self.round.save(update_fields=['prompt'])
        newer_round = Round.objects.create(
            season=self.season,
            prompt='Newest active prompt',
            goes_live_at=now - timedelta(hours=2),
            submission_opens=now - timedelta(hours=1),
            submission_deadline=now + timedelta(days=1),
            voting_deadline=now + timedelta(days=2),
            reveal_at=now + timedelta(days=3),
        )

        self.client.force_login(self.alice)
        response = self.client.get(reverse('home'))

        self.assertEqual(response.context['round'], newer_round)
        self.assertContains(response, 'Newest active prompt')
        self.assertNotContains(response, 'Older active prompt')

    def test_most_recent_revealed_round_is_selected_for_results(self):
        now = timezone.now()
        self.round.prompt = 'Older results prompt'
        self.round.submission_opens = now - timedelta(days=6)
        self.round.submission_deadline = now - timedelta(days=5)
        self.round.voting_deadline = now - timedelta(days=4)
        self.round.reveal_at = now - timedelta(days=3)
        self.round.save()
        newer_results = Round.objects.create(
            season=self.season,
            prompt='Newest results prompt',
            submission_opens=now - timedelta(days=4),
            submission_deadline=now - timedelta(days=3),
            voting_deadline=now - timedelta(days=2),
            reveal_at=now - timedelta(hours=1),
        )

        self.client.force_login(self.alice)
        response = self.client.get(reverse('home'))

        self.assertIsNone(response.context['round'])
        self.assertEqual(response.context['results_round'], newer_results)
        self.assertContains(response, 'Newest results prompt')
        self.assertNotContains(response, 'Older results prompt')

    def test_archive_includes_all_revealed_rounds_in_reverse_reveal_order(self):
        now = timezone.now()
        self.round.submission_opens = now - timedelta(days=6)
        self.round.submission_deadline = now - timedelta(days=5)
        self.round.voting_deadline = now - timedelta(days=4)
        self.round.reveal_at = now - timedelta(days=3)
        self.round.save()
        newer_archived = Round.objects.create(
            season=self.season,
            prompt='Newer archived result',
            submission_opens=now - timedelta(days=4),
            submission_deadline=now - timedelta(days=3),
            voting_deadline=now - timedelta(days=2),
            reveal_at=now - timedelta(hours=1),
            archived=True,
        )

        self.client.force_login(self.alice)
        response = self.client.get(reverse('archive'))

        self.assertEqual(
            list(response.context['rounds']),
            [newer_archived, self.round],
        )

    def test_hidden_and_draft_rounds_stay_off_home_and_archive(self):
        now = timezone.now()
        self.round.prompt = 'Hidden upcoming prompt'
        self.round.goes_live_at = now + timedelta(days=1)
        self.round.save(update_fields=['prompt', 'goes_live_at'])
        hidden_results = Round.objects.create(
            season=self.season,
            prompt='Hidden results prompt',
            goes_live_at=now + timedelta(days=1),
            submission_opens=now - timedelta(days=4),
            submission_deadline=now - timedelta(days=3),
            voting_deadline=now - timedelta(days=2),
            reveal_at=now - timedelta(days=1),
        )
        draft_results = Round.objects.create(
            season=self.season,
            prompt='Draft results prompt',
            submission_opens=now - timedelta(days=4),
            submission_deadline=now - timedelta(days=3),
            voting_deadline=now - timedelta(days=2),
            reveal_at=now - timedelta(days=1),
            is_draft=True,
        )

        self.client.force_login(self.alice)
        home_response = self.client.get(reverse('home'))
        archive_response = self.client.get(reverse('archive'))

        self.assertIsNone(home_response.context['round'])
        self.assertIsNone(home_response.context['results_round'])
        self.assertEqual(list(archive_response.context['rounds']), [])
        for rnd in (self.round, hidden_results, draft_results):
            self.assertNotContains(home_response, rnd.prompt)
            self.assertNotContains(archive_response, rnd.prompt)

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

        self.assertRedirects(response, reverse('control_rounds'))
        self.round.refresh_from_db()
        self.assertTrue(self.round.archived)

    def test_active_round_cannot_be_archived(self):
        staff = User.objects.create_user('staffer2', password='x', is_staff=True)
        self.client.force_login(staff)

        response = self.client.post(reverse('round_archive', args=[self.round.pk]))

        self.assertRedirects(response, reverse('control_rounds'))
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
        delivered_keys = set(
            NotificationDelivery.objects.values_list('event_key', flat=True)
        )
        call_command('send_round_notifications')
        self.assertEqual(mocked_push.call_count, first)
        self.assertEqual(NotificationDelivery.objects.count(), first)
        self.assertEqual(delivered_keys, {f'round:{self.round.pk}:live'})

    def test_submission_reminder_event_keeps_audience_key_and_payload(self):
        now = timezone.now()
        self.round.submission_opens = now - timedelta(days=1)
        self.round.submission_deadline = now + timedelta(hours=5)
        self.round.save()
        self.submission(self.alice, 'own')

        event = next(
            event for event in round_notification_events(self.round, now)
            if event.event_key.endswith(':submission-reminder-6h')
        )

        self.assertEqual(
            list(event.subscriptions.values_list('endpoint', flat=True)),
            ['https://push/b1'],
        )
        self.assertEqual(
            event.event_key,
            f'round:{self.round.pk}:submission-reminder-6h',
        )
        self.assertEqual(event.title, '6 hours left to submit')
        self.assertEqual(
            event.body,
            'Choose a clean song for “Prompt” before submissions close.',
        )
        self.assertEqual(event.url, f'/round/{self.round.pk}/')

    def test_reminders_keep_existing_six_hour_selection_window(self):
        now = timezone.now()
        self.round.submission_opens = now - timedelta(days=1)
        self.round.submission_deadline = now + timedelta(hours=6)
        self.round.voting_deadline = now + timedelta(days=1)
        self.round.save()

        at_boundary = {
            event.event_key for event in round_notification_events(
                self.round, now,
            )
        }
        before_boundary = {
            event.event_key for event in round_notification_events(
                self.round, now - timedelta(seconds=1),
            )
        }

        reminder_key = f'round:{self.round.pk}:submission-reminder-6h'
        self.assertIn(reminder_key, at_boundary)
        self.assertNotIn(reminder_key, before_boundary)

    def test_submission_reminder_audience_excludes_existing_submitters(self):
        self.submission(self.alice, 'own')

        endpoints = submission_reminder_audience(self.round).values_list(
            'endpoint', flat=True,
        )

        self.assertEqual(list(endpoints), ['https://push/b1'])

    def test_voting_reminder_uses_complete_ballot_semantics(self):
        alice_song = self.submission(self.alice, 'alice-song')
        bob_song = self.submission(self.bob, 'bob-song')
        cara_song = self.submission(self.cara, 'cara-song')
        Vote.objects.create(
            round=self.round, voter=self.alice,
            submission=bob_song, score=5,
        )
        Vote.objects.create(
            round=self.round, voter=self.alice,
            submission=cara_song, score=4,
        )
        Vote.objects.create(
            round=self.round, voter=self.bob,
            submission=alice_song, score=3,
        )

        audience = voting_reminder_audience(self.round)

        self.assertEqual(
            list(audience.values_list('endpoint', flat=True)),
            ['https://push/b1'],
        )

    def test_voting_reminder_event_keeps_key_and_payload(self):
        now = timezone.now()
        self.submission(self.alice, 'alice-song')
        self.submission(self.bob, 'bob-song')
        self.round.submission_deadline = now - timedelta(hours=1)
        self.round.voting_deadline = now + timedelta(hours=5)
        self.round.save()

        event = next(
            event for event in round_notification_events(self.round, now)
            if event.event_key.endswith(':voting-reminder-6h')
        )

        self.assertEqual(
            event.event_key,
            f'round:{self.round.pk}:voting-reminder-6h',
        )
        self.assertEqual(event.title, '6 hours left to vote')
        self.assertEqual(
            event.body,
            'Finish rating the songs for “Prompt”.',
        )
        self.assertEqual(event.url, f'/round/{self.round.pk}/')

    def test_results_event_keeps_global_audience_key_and_payload(self):
        now = timezone.now()
        self.round.reveal_at = now - timedelta(minutes=1)
        self.round.save(update_fields=['reveal_at'])

        event = next(
            event for event in round_notification_events(self.round, now)
            if event.event_key.endswith(':results')
        )

        self.assertEqual(
            set(event.subscriptions.values_list('endpoint', flat=True)),
            {'https://push/a1', 'https://push/a2', 'https://push/b1'},
        )
        self.assertEqual(event.event_key, f'round:{self.round.pk}:results')
        self.assertEqual(event.title, 'Results ready')
        self.assertEqual(event.body, 'See who won “Prompt”.')
        self.assertEqual(event.url, f'/round/{self.round.pk}/')

    def test_achievement_event_keeps_audience_key_and_payload(self):
        self.submission(self.alice, 'first-pick')

        events = [
            event for event in achievement_notification_events()
            if event.user == self.alice and event.body == 'Submit your first song'
        ]

        self.assertEqual(len(events), 1)
        event = events[0]
        unlock = AchievementUnlock.objects.get(user=self.alice, key='first_pick')
        self.assertEqual(event.event_key, f'achievement:{unlock.pk}')
        self.assertEqual(event.title, 'Badge unlocked')
        self.assertEqual(event.url, '/stats/alice/')

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

    @patch('league.push.webpush')
    def test_each_subscribed_device_receives_an_event_once(self, mocked_push):
        call_command('send_round_notifications')

        live_calls = [
            call for call in mocked_push.call_args_list
            if f'round:{self.round.pk}:live' in call.kwargs['data']
        ]
        self.assertEqual(
            {call.kwargs['subscription_info']['endpoint'] for call in live_calls},
            {'https://push/a1', 'https://push/a2', 'https://push/b1'},
        )
        self.assertEqual(len(live_calls), 3)


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

    @patch('league.views.send_user_push')
    def test_staff_can_approve_user_and_push_only_once(self, mocked_push):
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        player = User.objects.create_user('pending', password='x')
        self.client.force_login(staff)
        response = self.client.post(reverse('user_action', args=[player.pk, 'approve']))
        self.assertRedirects(response, reverse('control_users'))
        player.profile.refresh_from_db()
        self.assertTrue(player.profile.approved)
        self.assertIsNotNone(player.profile.approved_at)
        approved_at = player.profile.approved_at
        mocked_push.assert_called_once_with(
            player,
            f'user:{player.pk}:approved',
            'You’re approved!',
            'Your QueueUp account is ready. Tap to enter the league.',
            '/home/',
        )

        self.client.post(reverse('user_action', args=[player.pk, 'approve']))

        player.profile.refresh_from_db()
        self.assertEqual(player.profile.approved_at, approved_at)
        mocked_push.assert_called_once()

    def test_staff_can_deactivate_and_reactivate_user(self):
        staff = User.objects.create_user('active-staff', password='x', is_staff=True)
        player = User.objects.create_user('active-player', password='x')
        self.client.force_login(staff)
        action_url = reverse('user_action', args=[player.pk, 'toggle_active'])

        first = self.client.post(action_url)
        player.refresh_from_db()
        self.assertRedirects(first, reverse('control_users'))
        self.assertFalse(player.is_active)

        second = self.client.post(action_url)
        player.refresh_from_db()
        self.assertRedirects(second, reverse('control_users'))
        self.assertTrue(player.is_active)

    @patch('league.views.send_user_push')
    def test_staff_flag_toggle_preserves_automatic_approval_behavior(
        self, mocked_push,
    ):
        staff = User.objects.create_user('staff-manager', password='x', is_staff=True)
        player = User.objects.create_user('future-staff', password='x')
        self.client.force_login(staff)
        action_url = reverse('user_action', args=[player.pk, 'toggle_staff'])

        self.client.post(action_url)

        player.refresh_from_db()
        player.profile.refresh_from_db()
        self.assertTrue(player.is_staff)
        self.assertTrue(player.profile.approved)
        self.assertIsNotNone(player.profile.approved_at)
        mocked_push.assert_not_called()

        self.client.post(action_url)
        player.refresh_from_db()
        self.assertFalse(player.is_staff)
        self.assertTrue(player.profile.approved)

    def test_self_protection_actions_leave_staff_access_unchanged(self):
        staff = User.objects.create_user('protected-staff', password='x', is_staff=True)
        self.client.force_login(staff)

        for action in ('toggle_active', 'toggle_staff'):
            response = self.client.post(
                reverse('user_action', args=[staff.pk, action]),
                follow=True,
            )
            self.assertContains(response, 'You cannot remove your own access.')

        staff.refresh_from_db()
        self.assertTrue(staff.is_active)
        self.assertTrue(staff.is_staff)

    def test_players_search_still_filters_name_username_and_email(self):
        staff = User.objects.create_user('search-staff', password='x', is_staff=True)
        match = User.objects.create_user(
            'membership-match',
            first_name='Needle Name',
            email='needle@example.com',
            password='x',
        )
        other = User.objects.create_user('membership-other', password='x')
        self.client.force_login(staff)

        response = self.client.get(reverse('control_users'), {'q': 'needle'})

        self.assertEqual(list(response.context['users']), [match])
        self.assertContains(response, match.username)
        self.assertNotContains(response, other.username)


class MembershipAndBadgeCommandTests(QueueUpTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            'badge-staff', password='x', is_staff=True,
        )
        self.manual_badge = Badge.objects.create(
            name='Manual Honor',
            slug='manual-honor',
            description='Awarded manually.',
            icon='◆',
        )

    def test_manual_badge_can_be_awarded_and_removed(self):
        from .services.achievements import earned_badges, prestige_badges

        self.client.force_login(self.staff)
        url = reverse(
            'badge_award', args=[self.manual_badge.pk, self.alice.pk],
        )

        awarded_response = self.client.post(url, follow=True)

        award = UserBadge.objects.get(user=self.alice, badge=self.manual_badge)
        self.assertEqual(award.awarded_by, self.staff)
        self.assertContains(awarded_response, 'Awarded Manual Honor to alice.')
        manual_row = next(
            row for row in earned_badges(self.alice)
            if row['key'] == 'badge:manual-honor'
        )
        self.assertTrue(manual_row['earned'])
        self.assertIn(self.manual_badge, prestige_badges(self.alice))

        removed_response = self.client.post(url, follow=True)

        self.assertFalse(
            UserBadge.objects.filter(
                user=self.alice, badge=self.manual_badge,
            ).exists()
        )
        self.assertContains(removed_response, 'Removed Manual Honor from alice.')
        manual_row = next(
            row for row in earned_badges(self.alice)
            if row['key'] == 'badge:manual-honor'
        )
        self.assertFalse(manual_row['earned'])
        self.assertNotIn(self.manual_badge, prestige_badges(self.alice))

    def test_manual_award_does_not_override_automatic_achievement_logic(self):
        from .services.achievements import (
            achievement_checks,
            earned_badges,
            prestige_badges,
        )

        automatic_badge = Badge.objects.create(
            name='Automatic First Entry',
            slug='automatic-first-entry',
            description='Requires a submission.',
            achievement_key='first_pick',
        )
        self.client.force_login(self.staff)

        self.client.post(reverse(
            'badge_award', args=[automatic_badge.pk, self.alice.pk],
        ))

        self.assertTrue(
            UserBadge.objects.filter(
                user=self.alice, badge=automatic_badge,
            ).exists()
        )
        self.assertFalse(achievement_checks(self.alice)['first_pick'])
        automatic_row = next(
            row for row in earned_badges(self.alice)
            if row['key'] == 'first_pick'
        )
        self.assertFalse(automatic_row['earned'])
        self.assertNotIn(automatic_badge, prestige_badges(self.alice))

    def test_non_staff_cannot_mutate_membership_or_badges(self):
        target = User.objects.create_user('protected-player', password='x')
        self.client.force_login(self.alice)

        membership_response = self.client.post(reverse(
            'user_action', args=[target.pk, 'toggle_active'],
        ))
        badge_response = self.client.post(reverse(
            'badge_award', args=[self.manual_badge.pk, target.pk],
        ))

        target.refresh_from_db()
        self.assertEqual(membership_response.status_code, 302)
        self.assertIn(reverse('login'), membership_response.url)
        self.assertEqual(badge_response.status_code, 302)
        self.assertIn(reverse('login'), badge_response.url)
        self.assertTrue(target.is_active)
        self.assertFalse(
            UserBadge.objects.filter(
                user=target, badge=self.manual_badge,
            ).exists()
        )


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

        profile = profile_for_user(self.alice)
        self.assertEqual(list(profile.submissions), [])
        self.assertEqual(profile.round_count, 0)
        self.assertEqual(list(profile.seasons), [])

    def test_voting_round_keeps_submitters_anonymous(self):
        self.bob.first_name = 'Secret Submitter'
        self.bob.save(update_fields=['first_name'])
        self.submission(self.alice, 'own-secret')
        self.submission(self.bob, 'anonymous-song')
        self.client.force_login(self.alice)

        response = self.client.get(reverse('round_detail', args=[self.round.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mystery song')
        self.assertNotContains(response, 'Secret Submitter')
        self.assertNotContains(response, 'Picked by')

    def test_revealed_round_still_shows_results_and_submitters(self):
        self.bob.first_name = 'Revealed Submitter'
        self.bob.save(update_fields=['first_name'])
        alice_song = self.submission(self.alice, 'alice-result')
        bob_song = self.submission(self.bob, 'revealed-song')
        Vote.objects.create(
            round=self.round, voter=self.cara, submission=alice_song, score=4,
        )
        Vote.objects.create(
            round=self.round, voter=self.cara, submission=bob_song, score=5,
        )
        now = timezone.now()
        self.round.voting_deadline = now - timedelta(hours=2)
        self.round.reveal_at = now - timedelta(hours=1)
        self.round.save(update_fields=['voting_deadline', 'reveal_at'])
        self.client.force_login(self.alice)

        response = self.client.get(reverse('round_detail', args=[self.round.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'revealed-song')
        self.assertContains(response, 'Revealed Submitter')
        self.assertContains(response, 'Picked by')
        self.assertContains(response, '5.00 ★')

    def test_voting_guide_acknowledgement_is_persistent(self):
        self.client.force_login(self.alice)
        self.assertFalse(self.alice.profile.voting_guide_seen)
        self.assertContains(
            self.client.get(reverse('round_detail', args=[self.round.pk])),
            'data-voting-guide',
        )
        self.client.post(reverse('voting_guide_seen'))
        self.alice.profile.refresh_from_db()
        self.assertTrue(self.alice.profile.voting_guide_seen)
        self.assertNotContains(
            self.client.get(reverse('round_detail', args=[self.round.pk])),
            'data-voting-guide',
        )


class V70CleanMusicTests(QueueUpTestMixin, TestCase):
    @patch('league.services.submissions.api_get')
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
        profile_form = re.search(
            rf'<form[^>]+action="{re.escape(reverse("profile_edit"))}".*?</form>',
            content,
            re.DOTALL,
        ).group()
        upload_form = re.search(
            rf'<form[^>]+action="{re.escape(reverse("profile_picture_upload"))}".*?</form>',
            content,
            re.DOTALL,
        ).group()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'action="{reverse("profile_edit")}"')
        self.assertContains(response, f'action="{reverse("profile_picture_upload")}"')
        self.assertIn('name="csrfmiddlewaretoken"', profile_form)
        self.assertNotIn('name="csrfmiddlewaretoken"', upload_form)
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertIn('no-cache', response.headers.get('Cache-Control', ''))

    def test_csrf_exemption_is_isolated_to_picture_upload_endpoint(self):
        from .views import profile_edit, remove_profile_picture, upload_profile_picture

        self.assertTrue(upload_profile_picture.csrf_exempt)
        self.assertFalse(getattr(profile_edit, 'csrf_exempt', False))
        self.assertFalse(getattr(remove_profile_picture, 'csrf_exempt', False))

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

    def test_replacing_picture_deletes_old_stored_file(self):
        self.client.force_login(self.alice)
        first = SimpleUploadedFile(
            'first-profile-picture.png',
            self.png_bytes(),
            content_type='image/png',
        )
        self.client.post(
            reverse('profile_picture_upload'),
            {'picture': first},
        )
        self.alice.profile.refresh_from_db()
        old_name = self.alice.profile.picture.name
        storage = self.alice.profile.picture.storage
        self.assertTrue(storage.exists(old_name))

        second = SimpleUploadedFile(
            'replacement-profile-picture.jpg',
            self.jpeg_bytes(),
            content_type='image/jpeg',
        )
        response = self.client.post(
            reverse('profile_picture_upload'),
            {'picture': second},
        )

        self.assertRedirects(response, reverse('profile_edit'))
        self.alice.profile.refresh_from_db()
        new_name = self.alice.profile.picture.name
        self.assertNotEqual(new_name, old_name)
        self.assertFalse(storage.exists(old_name))
        self.assertTrue(storage.exists(new_name))
        self.alice.profile.picture.delete(save=False)

    def test_invalid_picture_does_not_replace_current_stored_file(self):
        self.client.force_login(self.alice)
        current = SimpleUploadedFile(
            'current-profile-picture.png',
            self.png_bytes(),
            content_type='image/png',
        )
        self.client.post(
            reverse('profile_picture_upload'),
            {'picture': current},
        )
        self.alice.profile.refresh_from_db()
        current_name = self.alice.profile.picture.name
        storage = self.alice.profile.picture.storage

        invalid = SimpleUploadedFile(
            'invalid-replacement.txt',
            b'not an image',
            content_type='text/plain',
        )
        response = self.client.post(
            reverse('profile_picture_upload'),
            {'picture': invalid},
        )

        self.assertRedirects(response, reverse('profile_edit'))
        self.alice.profile.refresh_from_db()
        self.assertEqual(self.alice.profile.picture.name, current_name)
        self.assertTrue(storage.exists(current_name))
        self.alice.profile.picture.delete(save=False)


    def test_remove_picture_is_in_picture_card_and_remains_csrf_protected(self):
        from django.test import Client
        picture = SimpleUploadedFile('avatar.png', self.png_bytes(), content_type='image/png')
        self.alice.profile.picture = picture
        self.alice.profile.save(update_fields=['picture', 'updated_at'])
        stored_name = self.alice.profile.picture.name
        storage = self.alice.profile.picture.storage
        self.assertTrue(storage.exists(stored_name))

        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(username='alice', password='x'))
        page = client.get(reverse('profile_edit'))
        remove_form = re.search(
            rf'<form[^>]+action="{re.escape(reverse("profile_picture_remove"))}".*?</form>',
            page.content.decode(),
            re.DOTALL,
        ).group()
        self.assertContains(page, f'action="{reverse("profile_picture_remove")}"')
        self.assertIn('name="csrfmiddlewaretoken"', remove_form)

        blocked = client.post(reverse('profile_picture_remove'))
        self.assertEqual(blocked.status_code, 403)
        self.alice.profile.refresh_from_db()
        self.assertTrue(bool(self.alice.profile.picture))

        token = page.cookies[settings.CSRF_COOKIE_NAME].value
        removed = client.post(reverse('profile_picture_remove'), {'csrfmiddlewaretoken': token})
        self.assertRedirects(removed, reverse('profile_edit'))
        self.alice.profile.refresh_from_db()
        self.assertFalse(bool(self.alice.profile.picture))
        self.assertFalse(storage.exists(stored_name))

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

    def test_control_rounds_requires_staff(self):
        self.client.force_login(self.alice)

        response = self.client.get(reverse('control_rounds'))

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

        self.assertEqual(
            [row['player'].username for row in response.context['rows']],
            ['alice', 'bob', 'cara', 'staff'],
        )
        rows = {row['player'].username: row for row in response.context['rows']}
        self.assertEqual(set(rows), {'alice', 'bob', 'cara', 'staff'})
        self.assertIsNotNone(rows['alice']['submission'])
        self.assertIsNotNone(rows['bob']['submission'])
        self.assertIsNotNone(rows['cara']['submission'])
        self.assertIsNone(rows['staff']['submission'])
        self.assertTrue(rows['alice']['voting_complete'])
        self.assertEqual(rows['alice']['voted_count'], 2)
        self.assertEqual(rows['alice']['eligible_count'], 2)
        self.assertFalse(rows['bob']['voting_complete'])
        self.assertEqual(rows['bob']['voted_count'], 1)
        self.assertEqual(rows['bob']['eligible_count'], 2)
        self.assertFalse(rows['cara']['voting_started'])
        self.assertFalse(rows['cara']['voting_complete'])
        self.assertFalse(rows['staff']['voting_started'])
        self.assertFalse(rows['staff']['voting_complete'])
        self.assertEqual(rows['staff']['voted_count'], 0)
        self.assertEqual(rows['staff']['eligible_count'], 3)
        self.assertEqual(response.context['player_count'], 4)
        self.assertEqual(response.context['submitted_count'], 3)
        self.assertEqual(response.context['completed_count'], 1)

    def test_control_rounds_links_each_round_to_status_page(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('control_rounds'))
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
        from .services.achievements import achievement_checks
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
        from .services.achievements import achievement_checks
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
        from .services.achievements import achievement_checks
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

class SubmissionWorkflowTests(QueueUpTestMixin, TestCase):
    first_track_id = 'AAAAAAAAAAAAAAAAAAAAAA'
    second_track_id = 'BBBBBBBBBBBBBBBBBBBBBB'

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.round.submission_opens = now - timedelta(hours=1)
        self.round.submission_deadline = now + timedelta(hours=1)
        self.round.save(update_fields=['submission_opens', 'submission_deadline'])
        self.client.force_login(self.alice)

    def accept_rules(self):
        self.alice.profile.submission_rules_accepted_at = timezone.now()
        self.alice.profile.save(update_fields=['submission_rules_accepted_at'])

    @staticmethod
    def spotify_track(track_id, *, isrc='USABC1234567', explicit=False):
        return {
            'id': track_id,
            'uri': f'spotify:track:{track_id}',
            'external_urls': {
                'spotify': f'https://open.spotify.com/track/{track_id}',
            },
            'external_ids': {'isrc': isrc},
            'name': 'Verified Song',
            'explicit': explicit,
            'artists': [
                {'id': 'artist-one', 'name': 'Artist One'},
                {'id': 'artist-two', 'name': 'Artist Two'},
            ],
            'album': {
                'name': 'Verified Album',
                'images': [{'url': 'https://images.example/cover.jpg'}],
            },
            'preview_url': 'https://audio.example/preview.mp3',
        }

    def assert_user_has_no_score(self):
        self.assertFalse(any(
            entry.item.pk == self.alice.pk
            for entry in season_leaderboard(self.season)
        ))

    @patch('league.views.broadcast')
    @patch(
        'league.services.submissions.genres_for_artists',
        return_value=['alternative rock'],
    )
    @patch('league.services.submissions.api_get')
    def test_success_preserves_metadata_bonus_and_realtime_event(
        self, mocked_get, mocked_genres, mocked_broadcast,
    ):
        self.accept_rules()
        mocked_get.return_value = self.spotify_track(self.first_track_id)

        response = self.client.post(
            reverse('submit_song', args=[self.round.pk]),
            {
                'track_id': (
                    f'https://open.spotify.com/track/{self.first_track_id}?si=test'
                ),
            },
            follow=True,
        )

        submission = Submission.objects.get(round=self.round, user=self.alice)
        self.assertEqual(submission.spotify_track_id, self.first_track_id)
        self.assertEqual(submission.isrc, 'USABC1234567')
        self.assertEqual(submission.spotify_uri, f'spotify:track:{self.first_track_id}')
        self.assertEqual(
            submission.spotify_url,
            f'https://open.spotify.com/track/{self.first_track_id}',
        )
        self.assertEqual(submission.title, 'Verified Song')
        self.assertEqual(submission.artist, 'Artist One, Artist Two')
        self.assertEqual(submission.artist_ids, ['artist-one', 'artist-two'])
        self.assertEqual(submission.genres, ['alternative rock'])
        self.assertEqual(submission.album, 'Verified Album')
        self.assertEqual(
            submission.album_art_url,
            'https://images.example/cover.jpg',
        )
        self.assertEqual(
            submission.preview_url,
            'https://audio.example/preview.mp3',
        )
        self.assertFalse(submission.explicit)
        mocked_get.assert_called_once_with(f'/tracks/{self.first_track_id}')
        mocked_genres.assert_called_once_with(['artist-one', 'artist-two'])
        mocked_broadcast.assert_called_once_with(
            'submission_added',
            round_id=self.round.id,
            submissions=1,
        )
        player = next(
            entry.item
            for entry in season_leaderboard(self.season)
            if entry.item.pk == self.alice.pk
        )
        self.assertEqual(player.submission_bonus, SUBMISSION_BONUS_POINTS)
        self.assertEqual(player.total_score, SUBMISSION_BONUS_POINTS)
        self.assertContains(
            response,
            f'You earned {SUBMISSION_BONUS_POINTS} points for submitting!',
        )

    @patch('league.views.broadcast')
    @patch('league.services.submissions.genres_for_artists', return_value=[])
    @patch('league.services.submissions.api_get')
    def test_deleting_and_replacing_submission_restores_bonus(
        self, mocked_get, _mocked_genres, mocked_broadcast,
    ):
        self.accept_rules()
        mocked_get.side_effect = [
            self.spotify_track(self.first_track_id, isrc='USFIRST12345'),
            self.spotify_track(self.second_track_id, isrc='USSECOND1234'),
        ]
        submit_url = reverse('submit_song', args=[self.round.pk])

        self.client.post(submit_url, {'track_id': self.first_track_id})
        first = Submission.objects.get(round=self.round, user=self.alice)
        self.assertEqual(
            next(
                entry.item
                for entry in season_leaderboard(self.season)
                if entry.item.pk == self.alice.pk
            ).submission_bonus,
            SUBMISSION_BONUS_POINTS,
        )

        first.delete()
        self.assert_user_has_no_score()

        self.client.post(submit_url, {'track_id': self.second_track_id})
        replacement = Submission.objects.get(round=self.round, user=self.alice)
        self.assertEqual(replacement.spotify_track_id, self.second_track_id)
        self.assertEqual(
            next(
                entry.item
                for entry in season_leaderboard(self.season)
                if entry.item.pk == self.alice.pk
            ).submission_bonus,
            SUBMISSION_BONUS_POINTS,
        )
        self.assertEqual(mocked_broadcast.call_count, 2)

    @patch('league.views.broadcast')
    @patch('league.services.submissions.api_get')
    def test_submission_outside_allowed_phase_is_rejected(
        self, mocked_get, mocked_broadcast,
    ):
        self.round.submission_deadline = timezone.now() - timedelta(minutes=1)
        self.round.save(update_fields=['submission_deadline'])

        response = self.client.post(
            reverse('submit_song', args=[self.round.pk]),
            {'track_id': self.first_track_id},
            follow=True,
        )

        self.assertContains(response, 'Submissions are closed.')
        self.assertFalse(Submission.objects.filter(round=self.round).exists())
        self.assert_user_has_no_score()
        mocked_get.assert_not_called()
        mocked_broadcast.assert_not_called()

    @patch('league.views.broadcast')
    @patch('league.services.submissions.genres_for_artists')
    @patch('league.services.submissions.api_get')
    def test_submission_rules_acceptance_remains_required(
        self, mocked_get, mocked_genres, mocked_broadcast,
    ):
        mocked_get.return_value = self.spotify_track(self.first_track_id)

        response = self.client.post(
            reverse('submit_song', args=[self.round.pk]),
            {'track_id': self.first_track_id},
            follow=True,
        )

        self.assertContains(
            response,
            'Please confirm the clean, all-ages music rule before submitting.',
        )
        self.assertFalse(Submission.objects.filter(round=self.round).exists())
        self.assert_user_has_no_score()
        mocked_genres.assert_not_called()
        mocked_broadcast.assert_not_called()

    @patch('league.views.broadcast')
    @patch('league.services.submissions.api_get')
    def test_spotify_refetch_failure_creates_nothing_and_awards_no_points(
        self, mocked_get, mocked_broadcast,
    ):
        self.accept_rules()
        mocked_get.side_effect = RuntimeError('Spotify unavailable')

        response = self.client.post(
            reverse('submit_song', args=[self.round.pk]),
            {'track_id': self.first_track_id},
            follow=True,
        )

        self.assertContains(response, 'That Spotify track could not be verified.')
        self.assertFalse(Submission.objects.filter(round=self.round).exists())
        self.assert_user_has_no_score()
        mocked_broadcast.assert_not_called()

    @patch('league.views.broadcast')
    @patch('league.services.submissions.genres_for_artists')
    @patch('league.services.submissions.api_get')
    def test_explicit_track_creates_nothing_and_awards_no_points(
        self, mocked_get, mocked_genres, mocked_broadcast,
    ):
        self.accept_rules()
        mocked_get.return_value = self.spotify_track(
            self.first_track_id,
            explicit=True,
        )

        response = self.client.post(
            reverse('submit_song', args=[self.round.pk]),
            {'track_id': self.first_track_id},
            follow=True,
        )

        self.assertContains(response, 'No explicit songs allowed.')
        self.assertFalse(Submission.objects.filter(round=self.round).exists())
        self.assert_user_has_no_score()
        mocked_genres.assert_not_called()
        mocked_broadcast.assert_not_called()

    @patch('league.views.broadcast')
    @patch('league.services.submissions.genres_for_artists', return_value=[])
    @patch('league.services.submissions.api_get')
    def test_same_isrc_duplicate_creates_nothing_and_awards_no_points(
        self, mocked_get, _mocked_genres, mocked_broadcast,
    ):
        existing = self.submission(self.bob, 'existing-track')
        existing.isrc = 'USABC1234567'
        existing.save(update_fields=['isrc'])
        self.accept_rules()
        mocked_get.return_value = self.spotify_track(self.first_track_id)

        response = self.client.post(
            reverse('submit_song', args=[self.round.pk]),
            {'track_id': self.first_track_id},
            follow=True,
        )

        self.assertContains(
            response,
            'That recording has already been submitted for this round.',
        )
        self.assertFalse(
            Submission.objects.filter(round=self.round, user=self.alice).exists()
        )
        self.assert_user_has_no_score()
        mocked_broadcast.assert_not_called()

    @patch('league.views.broadcast')
    @patch('league.services.submissions.genres_for_artists', return_value=[])
    @patch('league.services.submissions.api_get')
    def test_second_submission_by_same_user_preserves_existing_submission(
        self, mocked_get, _mocked_genres, mocked_broadcast,
    ):
        self.accept_rules()
        mocked_get.side_effect = [
            self.spotify_track(self.first_track_id, isrc='USFIRST12345'),
            self.spotify_track(self.second_track_id, isrc='USSECOND1234'),
        ]
        submit_url = reverse('submit_song', args=[self.round.pk])
        self.client.post(submit_url, {'track_id': self.first_track_id})

        response = self.client.post(
            submit_url,
            {'track_id': self.second_track_id},
            follow=True,
        )

        submissions = Submission.objects.filter(round=self.round, user=self.alice)
        self.assertEqual(submissions.count(), 1)
        self.assertEqual(submissions.get().spotify_track_id, self.first_track_id)
        self.assertContains(
            response,
            'You already submitted, or somebody chose that song first.',
        )
        player = next(
            entry.item
            for entry in season_leaderboard(self.season)
            if entry.item.pk == self.alice.pk
        )
        self.assertEqual(player.submission_bonus, SUBMISSION_BONUS_POINTS)
        mocked_broadcast.assert_called_once()


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

    @patch('league.services.submissions.genres_for_artists', return_value=[])
    @patch('league.services.submissions.api_get')
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

    @patch('league.services.submissions.genres_for_artists', return_value=[])
    @patch('league.services.submissions.api_get')
    def test_new_submission_saves_normalized_isrc(self, mocked_get, mocked_genres):
        mocked_get.return_value = self.spotify_track('CCCCCCCCCCCCCCCCCCCCCC', 'New Recording', 'usxyz7654321')
        self.client.force_login(self.alice)
        self.client.post(reverse('submit_song', args=[self.round.pk]), {'track_id': 'CCCCCCCCCCCCCCCCCCCCCC'})
        submission = Submission.objects.get(round=self.round, user=self.alice)
        self.assertEqual(submission.isrc, 'USXYZ7654321')

class RoundLifecycleCommandTests(QueueUpTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            'lifecycle-staff', password='x', is_staff=True,
        )
        self.client.force_login(self.staff)

    def create_round_history(self):
        alice_song = self.submission(self.alice, 'lifecycle-alice')
        bob_song = self.submission(self.bob, 'lifecycle-bob')
        vote = Vote.objects.create(
            round=self.round,
            voter=self.cara,
            submission=alice_song,
            score=4,
        )
        return alice_song.pk, bob_song.pk, vote.pk

    def assert_round_history_preserved(self, history_ids):
        alice_id, bob_id, vote_id = history_ids
        self.assertTrue(Submission.objects.filter(pk=alice_id).exists())
        self.assertTrue(Submission.objects.filter(pk=bob_id).exists())
        self.assertTrue(Vote.objects.filter(pk=vote_id, score=4).exists())

    def run_action(self, action, fixed_now):
        with (
            patch(
                'league.services.rounds.timezone.now',
                return_value=fixed_now,
            ),
            patch('league.views.broadcast') as mocked_broadcast,
        ):
            response = self.client.post(
                reverse('round_action', args=[self.round.pk, action]),
            )
        self.assertRedirects(
            response,
            reverse('control_rounds'),
            fetch_redirect_response=False,
        )
        self.round.refresh_from_db()
        return mocked_broadcast

    def test_open_submissions_sets_current_time_and_expired_default_duration(self):
        fixed_now = timezone.now()
        original_voting_deadline = fixed_now + timedelta(days=5)
        original_reveal_at = fixed_now + timedelta(days=6)
        self.round.submission_opens = fixed_now + timedelta(days=1)
        self.round.submission_deadline = fixed_now - timedelta(minutes=1)
        self.round.voting_deadline = original_voting_deadline
        self.round.reveal_at = original_reveal_at
        self.round.save()
        history_ids = self.create_round_history()

        mocked_broadcast = self.run_action('open_submissions', fixed_now)

        self.assertEqual(self.round.submission_opens, fixed_now)
        self.assertEqual(
            self.round.submission_deadline,
            fixed_now + timedelta(days=3),
        )
        self.assertEqual(self.round.voting_deadline, original_voting_deadline)
        self.assertEqual(self.round.reveal_at, original_reveal_at)
        self.assert_round_history_preserved(history_ids)
        mocked_broadcast.assert_called_once_with(
            'round_updated', round_id=self.round.id, state='submitting',
        )

    def test_open_voting_sets_current_time_and_expired_default_duration(self):
        fixed_now = timezone.now()
        original_opens = fixed_now - timedelta(days=3)
        original_reveal_at = fixed_now + timedelta(days=6)
        self.round.submission_opens = original_opens
        self.round.submission_deadline = fixed_now + timedelta(days=1)
        self.round.voting_deadline = fixed_now - timedelta(minutes=1)
        self.round.reveal_at = original_reveal_at
        self.round.save()
        history_ids = self.create_round_history()

        mocked_broadcast = self.run_action('open_voting', fixed_now)

        self.assertEqual(self.round.submission_opens, original_opens)
        self.assertEqual(self.round.submission_deadline, fixed_now)
        self.assertEqual(
            self.round.voting_deadline,
            fixed_now + timedelta(days=2),
        )
        self.assertEqual(self.round.reveal_at, original_reveal_at)
        self.assert_round_history_preserved(history_ids)
        mocked_broadcast.assert_called_once_with(
            'round_updated', round_id=self.round.id, state='voting',
        )

    def test_lock_voting_sets_current_time_and_expired_reveal_default(self):
        fixed_now = timezone.now()
        original_opens = fixed_now - timedelta(days=3)
        original_submission_deadline = fixed_now - timedelta(days=1)
        self.round.submission_opens = original_opens
        self.round.submission_deadline = original_submission_deadline
        self.round.voting_deadline = fixed_now + timedelta(days=1)
        self.round.reveal_at = fixed_now - timedelta(minutes=1)
        self.round.save()
        history_ids = self.create_round_history()

        mocked_broadcast = self.run_action('lock_voting', fixed_now)

        self.assertEqual(self.round.submission_opens, original_opens)
        self.assertEqual(
            self.round.submission_deadline,
            original_submission_deadline,
        )
        self.assertEqual(self.round.voting_deadline, fixed_now)
        self.assertEqual(
            self.round.reveal_at,
            fixed_now + timedelta(minutes=5),
        )
        self.assert_round_history_preserved(history_ids)
        mocked_broadcast.assert_called_once_with(
            'round_updated', round_id=self.round.id, state='locked',
        )

    def test_reveal_sets_only_reveal_time_and_preserves_history(self):
        fixed_now = timezone.now()
        original_opens = fixed_now - timedelta(days=4)
        original_submission_deadline = fixed_now - timedelta(days=3)
        original_voting_deadline = fixed_now - timedelta(days=2)
        self.round.submission_opens = original_opens
        self.round.submission_deadline = original_submission_deadline
        self.round.voting_deadline = original_voting_deadline
        self.round.reveal_at = fixed_now + timedelta(days=1)
        self.round.save()
        history_ids = self.create_round_history()

        mocked_broadcast = self.run_action('reveal', fixed_now)

        self.assertEqual(self.round.submission_opens, original_opens)
        self.assertEqual(
            self.round.submission_deadline,
            original_submission_deadline,
        )
        self.assertEqual(self.round.voting_deadline, original_voting_deadline)
        self.assertEqual(self.round.reveal_at, fixed_now)
        self.assert_round_history_preserved(history_ids)
        mocked_broadcast.assert_called_once_with(
            'round_updated', round_id=self.round.id, state='revealed',
        )

    def test_reveal_does_not_rewrite_future_voting_deadline(self):
        fixed_now = timezone.now()
        future_voting_deadline = fixed_now + timedelta(days=1)
        self.round.submission_opens = fixed_now - timedelta(days=2)
        self.round.submission_deadline = fixed_now - timedelta(days=1)
        self.round.voting_deadline = future_voting_deadline
        self.round.reveal_at = fixed_now + timedelta(days=2)
        self.round.save()

        mocked_broadcast = self.run_action('reveal', fixed_now)

        self.assertEqual(self.round.voting_deadline, future_voting_deadline)
        self.assertEqual(self.round.reveal_at, fixed_now)
        mocked_broadcast.assert_called_once_with(
            'round_updated', round_id=self.round.id, state='voting',
        )

    def test_archive_preserves_schedule_submissions_votes_and_broadcast(self):
        fixed_now = timezone.now()
        self.round.submission_opens = fixed_now - timedelta(days=4)
        self.round.submission_deadline = fixed_now - timedelta(days=3)
        self.round.voting_deadline = fixed_now - timedelta(days=2)
        self.round.reveal_at = fixed_now - timedelta(days=1)
        self.round.save()
        original_schedule = (
            self.round.submission_opens,
            self.round.submission_deadline,
            self.round.voting_deadline,
            self.round.reveal_at,
        )
        history_ids = self.create_round_history()

        with (
            patch(
                'league.services.rounds.timezone.now',
                return_value=fixed_now,
            ),
            patch('league.views.broadcast') as mocked_broadcast,
        ):
            response = self.client.post(
                reverse('round_archive', args=[self.round.pk]),
            )

        self.assertRedirects(
            response,
            reverse('control_rounds'),
            fetch_redirect_response=False,
        )
        self.round.refresh_from_db()
        self.assertTrue(self.round.archived)
        self.assertEqual(
            (
                self.round.submission_opens,
                self.round.submission_deadline,
                self.round.voting_deadline,
                self.round.reveal_at,
            ),
            original_schedule,
        )
        self.assert_round_history_preserved(history_ids)
        mocked_broadcast.assert_called_once_with(
            'round_updated', round_id=self.round.id, state='revealed',
        )


class AdminManagementAndSubmissionBonusTests(QueueUpTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user('staff-admin', password='x', is_staff=True)
        self.client.force_login(self.staff)

    def round_form_data(self, **overrides):
        now = timezone.localtime().replace(second=0, microsecond=0)
        data = {
            'season': self.season.pk,
            'prompt': 'A drafted prompt',
            'details': 'Draft details',
            'goes_live_at': (now + timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M'),
            'submission_opens': (now + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M'),
            'submission_deadline': (now + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
            'voting_deadline': (now + timedelta(days=4)).strftime('%Y-%m-%dT%H:%M'),
            'reveal_at': (now + timedelta(days=5)).strftime('%Y-%m-%dT%H:%M'),
            'host': '',
            'playlist_url': '',
        }
        data.update(overrides)
        return data

    def test_round_form_offers_all_three_save_actions(self):
        response = self.client.get(reverse('round_create'))

        self.assertContains(response, 'value="draft"')
        self.assertContains(response, 'Save as draft')
        self.assertContains(response, 'value="save"')
        self.assertContains(response, 'Save and publish')

    def test_save_as_draft_keeps_round_staff_only_regardless_of_schedule(self):
        data = self.round_form_data(
            save_action='draft',
            goes_live_at=(timezone.localtime() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
        )
        response = self.client.post(reverse('round_create'), data)

        self.assertRedirects(response, reverse('control_rounds'))
        drafted = Round.objects.get(prompt='A drafted prompt')
        self.assertTrue(drafted.is_draft)
        self.assertEqual(drafted.state, 'draft')
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('round_detail', args=[drafted.pk])).status_code, 404)
        self.assertNotEqual(self.client.get(reverse('home')).context['round'], drafted)

    def test_save_activates_draft_but_keeps_scheduled_go_live_time(self):
        scheduled_live = timezone.now() + timedelta(hours=3)
        self.round.is_draft = True
        self.round.save(update_fields=['is_draft'])
        data = self.round_form_data(
            prompt=self.round.prompt,
            save_action='save',
            goes_live_at=timezone.localtime(scheduled_live).strftime('%Y-%m-%dT%H:%M'),
        )

        self.client.post(reverse('round_edit', args=[self.round.pk]), data)

        self.round.refresh_from_db()
        self.assertFalse(self.round.is_draft)
        self.assertAlmostEqual(self.round.goes_live_at.timestamp(), scheduled_live.replace(second=0, microsecond=0).timestamp(), delta=1)
        self.assertFalse(self.round.is_visible)

    @patch('league.views.broadcast')
    def test_save_and_publish_activates_draft_and_overrides_go_live_time(
        self, mocked_broadcast,
    ):
        self.round.is_draft = True
        self.round.save(update_fields=['is_draft'])
        before = timezone.now()

        response = self.client.post(
            reverse('round_edit', args=[self.round.pk]),
            self.round_form_data(prompt=self.round.prompt, save_action='publish'),
        )

        self.assertRedirects(response, reverse('control_rounds'))
        self.round.refresh_from_db()
        self.assertFalse(self.round.is_draft)
        self.assertGreaterEqual(self.round.goes_live_at, before - timedelta(seconds=1))
        self.assertLessEqual(self.round.goes_live_at, timezone.now() + timedelta(seconds=1))
        self.assertTrue(self.round.is_visible)
        mocked_broadcast.assert_called_once_with(
            'round_updated', round_id=self.round.id, state='upcoming',
        )

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
        third = Submission.objects.create(
            round=self.round, user=self.cara, spotify_track_id='count-c',
            spotify_uri='spotify:track:count-c',
            spotify_url='https://open.spotify.com/track/count-c',
            title='Third', artist='Artist',
        )
        Vote.objects.create(round=self.round, voter=self.bob, submission=first, score=4)
        Vote.objects.create(round=self.round, voter=self.alice, submission=second, score=5)
        Vote.objects.create(round=self.round, voter=self.cara, submission=first, score=3)
        Vote.objects.create(round=self.round, voter=self.alice, submission=third, score=4)
        Vote.objects.create(round=self.round, voter=self.bob, submission=third, score=5)
        Vote.objects.create(round=self.round, voter=self.cara, submission=second, score=4)

        response = self.client.get(reverse('control_rounds'))
        row = next(r for r in response.context['rounds'] if r.pk == self.round.pk)
        self.assertEqual(Submission.objects.filter(round=self.round).count(), 3)
        self.assertEqual(Vote.objects.filter(round=self.round).count(), 6)
        self.assertEqual(row.submission_count, 3)
        self.assertEqual(row.submitted_player_count, 3)
        self.assertEqual(row.vote_count, 6)

    def test_round_cards_show_submission_and_completed_voter_progress(self):
        inactive = User.objects.create_user(
            'inactive-approved', password='x', is_active=False,
        )
        inactive.profile.approved = True
        inactive.profile.save(update_fields=['approved'])
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
        self.assertContains(response, '<b>2/4</b> people submitted')
        self.assertContains(response, '<b>3/4</b> people voted')

    def test_admin_sections_are_separate_and_searchable(self):
        response = self.client.get(reverse('control_panel'))
        self.assertContains(response, reverse('control_rounds'))
        self.assertContains(response, reverse('control_badges'))
        self.assertContains(response, reverse('control_users'))

        round_response = self.client.get(reverse('control_rounds'), {'q': self.round.prompt})
        self.assertContains(round_response, self.round.prompt)
        no_round_response = self.client.get(reverse('control_rounds'), {'q': 'not-a-real-prompt'})
        self.assertEqual(list(no_round_response.context['rounds']), [])

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
