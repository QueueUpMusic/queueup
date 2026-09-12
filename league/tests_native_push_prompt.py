import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse


class NativePushPromptTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('push-prompt-user', password='password')
        self.user.profile.approved = True
        self.user.profile.save(update_fields=['approved'])

    def post(self, client, name, payload=None):
        csrf = client.get(reverse('api-v1:auth-csrf')).cookies['queueup_csrftoken_v2'].value
        return client.post(
            reverse(name),
            data=json.dumps(payload or {}),
            content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf,
        )

    def test_state_reports_unseen_and_acknowledgement_persists(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        state = client.get(reverse('api-v1:onboarding')).json()['data']
        self.assertFalse(state['native_push_prompt_seen'])
        response = self.post(client, 'api-v1:native-push-prompt')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertIsNotNone(self.user.profile.native_push_prompt_seen_at)
        state = client.get(reverse('api-v1:onboarding')).json()['data']
        self.assertTrue(state['native_push_prompt_seen'])
