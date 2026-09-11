import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from .models import NativePushDevice, NativeNotificationDelivery
from .services.native_push import send_native_push


class NativePushApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('native-user', password='x')
        self.user.profile.approved = True
        self.user.profile.save(update_fields=['approved'])
        self.other = User.objects.create_user('native-other', password='x')
        self.other.profile.approved = True
        self.other.profile.save(update_fields=['approved'])

    def post(self, client, name, payload):
        csrf = client.get(reverse('api-v1:auth-csrf')).cookies['queueup_csrftoken_v2'].value
        return client.post(reverse(name), data=json.dumps(payload), content_type='application/json', HTTP_X_CSRFTOKEN=csrf)

    def test_registration_requires_authentication_and_valid_token(self):
        client = Client(enforce_csrf_checks=True)
        response = self.post(client, 'api-v1:mobile-push-register', {'expo_push_token': 'ExponentPushToken[abc]', 'platform': 'android'})
        self.assertEqual(response.status_code, 401)
        client.force_login(self.user)
        response = self.post(client, 'api-v1:mobile-push-register', {'expo_push_token': 'bad', 'platform': 'android'})
        self.assertEqual(response.status_code, 400)

    def test_registration_is_idempotent_and_reassigns_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        payload = {'expo_push_token': 'ExponentPushToken[abc]', 'platform': 'android', 'app_version': '1.0'}
        self.assertEqual(self.post(client, 'api-v1:mobile-push-register', payload).status_code, 200)
        self.assertEqual(self.post(client, 'api-v1:mobile-push-register', payload).status_code, 200)
        self.assertEqual(NativePushDevice.objects.count(), 1)
        client.force_login(self.other)
        self.assertEqual(self.post(client, 'api-v1:mobile-push-register', payload).status_code, 200)
        self.assertEqual(NativePushDevice.objects.get().user, self.other)

    def test_unregister_only_removes_authenticated_users_device(self):
        device = NativePushDevice.objects.create(user=self.user, expo_push_token='ExpoPushToken[abc]', platform='ios')
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.other)
        self.post(client, 'api-v1:mobile-push-unregister', {'expo_push_token': device.expo_push_token})
        self.assertTrue(NativePushDevice.objects.filter(pk=device.pk).exists())
        client.force_login(self.user)
        self.post(client, 'api-v1:mobile-push-unregister', {'expo_push_token': device.expo_push_token})
        self.assertFalse(NativePushDevice.objects.filter(pk=device.pk).exists())

    @patch('requests.post')
    def test_native_delivery_is_deduplicated_and_invalid_devices_are_disabled(self, post):
        device = NativePushDevice.objects.create(user=self.user, expo_push_token='ExpoPushToken[abc]', platform='android')
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {'data': [{'status': 'ok', 'id': 'ticket-1'}]}
        first = send_native_push([self.user], 'round:1:voting-open', 'Voting open', 'Rate songs')
        second = send_native_push([self.user], 'round:1:voting-open', 'Voting open', 'Rate songs')
        self.assertEqual(first['sent'], 1)
        self.assertEqual(second['skipped'], 1)
        self.assertEqual(NativeNotificationDelivery.objects.count(), 1)
        post.return_value.json.return_value = {'data': [{'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}]}
        send_native_push([self.user], 'round:2:results', 'Results ready', 'See results')
        device.refresh_from_db()
        self.assertFalse(device.enabled)
