from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import NativeNotificationDelivery, NativePushDevice
from .services.native_push import register_device, send_native_push, unregister_device


class NativePushInstallationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('native-rework-user', password='password')
        self.other = User.objects.create_user('native-rework-other', password='password')

    def register(self, user=None, token='ExponentPushToken[first]', installation='install-1'):
        return register_device(user or self.user, {
            'expo_push_token': token,
            'installation_id': installation,
            'platform': 'android',
            'app_version': '1.0',
        })

    def test_registration_requires_installation_id_and_enables_preference(self):
        from .services.native_push import InvalidNativePushDevice
        with self.assertRaises(InvalidNativePushDevice):
            register_device(self.user, {'expo_push_token': 'ExponentPushToken[x]', 'platform': 'android'})
        device = self.register()
        self.assertTrue(self.user.profile.native_notifications_enabled)
        self.assertEqual(NativePushDevice.objects.count(), 1)
        self.assertEqual(device.installation_id, 'install-1')

    def test_token_rollover_and_user_reassignment_reuse_installation(self):
        device = self.register()
        original_registered_at = device.registered_at
        updated = self.register(token='ExponentPushToken[second]')
        self.assertEqual(updated.pk, device.pk)
        self.assertEqual(NativePushDevice.objects.count(), 1)
        self.assertEqual(updated.registered_at, original_registered_at)
        reassigned = self.register(self.other, token='ExponentPushToken[third]')
        self.assertEqual(reassigned.pk, device.pk)
        self.assertEqual(reassigned.user, self.other)

    def test_disable_preserves_installation_but_turns_preference_off(self):
        device = self.register()
        unregister_device(self.user, device.expo_push_token, disable_preference=True)
        device.refresh_from_db()
        self.user.profile.refresh_from_db()
        self.assertFalse(device.enabled)
        self.assertFalse(self.user.profile.native_notifications_enabled)

    @patch('requests.post')
    def test_registered_boundary_blocks_history_and_deduplicates_new_event(self, post):
        device = self.register()
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {'data': [{'status': 'ok', 'id': 'ticket-1'}]}
        old = device.registered_at - timedelta(minutes=1)
        self.assertEqual(send_native_push(self.user, 'round:old', 'Old', 'Old', event_at=old)['devices_targeted'], 0)
        self.assertEqual(send_native_push(self.user, 'round:new', 'New', 'New', event_at=timezone.now())['sent'], 1)
        self.assertEqual(send_native_push(self.user, 'round:new', 'New', 'New', event_at=timezone.now())['skipped'], 1)
        self.assertEqual(NativeNotificationDelivery.objects.count(), 1)
        post.assert_called_once()

    @patch('requests.post')
    def test_invalid_expo_token_disables_installation(self, post):
        device = self.register()
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {'data': [{'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}]}
        result = send_native_push(self.user, 'round:invalid', 'Test', 'Test')
        device.refresh_from_db()
        self.assertEqual(result['removed'], 1)
        self.assertFalse(device.enabled)
        self.assertIsNotNone(device.invalidated_at)
