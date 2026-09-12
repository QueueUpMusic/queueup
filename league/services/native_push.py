import re
import uuid

from django.db import transaction
from django.utils import timezone

from ..models import NativeNotificationDelivery, NativePushDevice

EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'
TOKEN_RE = re.compile(r'^(?:Expo|Exponent)PushToken\[[^\]]+\]$')


class InvalidNativePushDevice(Exception):
    pass


def _text(value, limit):
    return str(value or '')[:limit]


@transaction.atomic
def register_device(user, data):
    token = data.get('expo_push_token')
    platform = data.get('platform')
    installation_id = data.get('installation_id')
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise InvalidNativePushDevice('A valid Expo push token is required.')
    if platform not in NativePushDevice.Platform.values:
        raise InvalidNativePushDevice('Platform must be ios or android.')
    if not isinstance(installation_id, str) or not installation_id.strip():
        raise InvalidNativePushDevice('A stable installation_id is required.')

    now = timezone.now()
    device = NativePushDevice.objects.select_for_update().filter(installation_id=installation_id).first()
    token_device = NativePushDevice.objects.select_for_update().filter(expo_push_token=token).first()
    if device is None:
        device = token_device
    elif token_device is not None and token_device.pk != device.pk:
        token_device.expo_push_token = f'retired:{uuid.uuid4().hex}'
        token_device.enabled = False
        token_device.disabled_at = now
        token_device.save(update_fields=['expo_push_token', 'enabled', 'disabled_at', 'updated_at'])

    if device is None:
        device = NativePushDevice(user=user, installation_id=installation_id, expo_push_token=token, platform=platform, registered_at=now)
    was_active = device.enabled and device.invalidated_at is None and device.registered_at is not None
    device.user = user
    device.installation_id = installation_id
    device.expo_push_token = token
    device.platform = platform
    device.device_id = _text(data.get('device_id'), 255)
    device.app_version = _text(data.get('app_version'), 64)
    device.enabled = True
    device.disabled_at = None
    device.invalidated_at = None
    if not was_active:
        device.registered_at = now
    device.last_seen_at = now
    device.save()
    user.profile.native_notifications_enabled = True
    user.profile.save(update_fields=['native_notifications_enabled', 'updated_at'])
    return device


def unregister_device(user, token, *, disable_preference=False):
    device = NativePushDevice.objects.filter(user=user, expo_push_token=token).first()
    if device:
        device.enabled = False
        device.disabled_at = timezone.now()
        device.save(update_fields=['enabled', 'disabled_at', 'updated_at'])
    if disable_preference:
        user.profile.native_notifications_enabled = False
        user.profile.save(update_fields=['native_notifications_enabled', 'updated_at'])
    return bool(device)


def device_registered(user, token):
    if not isinstance(token, str) or not token:
        return False
    return NativePushDevice.objects.filter(user=user, expo_push_token=token, enabled=True, invalidated_at__isnull=True, registered_at__isnull=False).exists()


def send_native_push(users, event_key, title, body, route='/home/', *, event_at=None, stderr=None, device_ids=None):
    if hasattr(users, 'pk'):
        users = [users]
    devices = NativePushDevice.objects.filter(
        user__in=users,
        user__is_active=True,
        user__profile__native_notifications_enabled=True,
        enabled=True,
        invalidated_at__isnull=True,
        registered_at__isnull=False,
    ).select_related('user')
    if device_ids is not None:
        devices = devices.filter(pk__in=device_ids)
    if event_at is not None:
        devices = devices.filter(registered_at__lte=event_at)
    result = {'devices_targeted': devices.count(), 'sent': 0, 'skipped': 0, 'failed': 0, 'removed': 0}
    pending = []
    for device in devices:
        delivery, created = NativeNotificationDelivery.objects.get_or_create(device=device, event_key=event_key, defaults={'user': device.user, 'title': title, 'body': body})
        if not created and delivery.status in ('sent', 'invalid'):
            result['skipped'] += 1
        else:
            pending.append((device, delivery))
    if not pending:
        return result
    try:
        import requests
        response = requests.post(EXPO_PUSH_URL, json=[{'to': device.expo_push_token, 'title': title, 'body': body, 'data': {'type': event_key.split(':', 1)[0], 'event_key': event_key, 'route': route}, 'sound': 'default'} for device, _ in pending], timeout=10)
        response.raise_for_status()
        tickets = response.json().get('data', [])
    except Exception as exc:
        for _, delivery in pending:
            delivery.status = 'failed'; delivery.error = str(exc)[:500]; delivery.save(update_fields=['status', 'error'])
        result['failed'] = len(pending)
        if stderr:
            stderr.write(f'Native push transport failed: {str(exc)[:200]}\n')
        return result
    for index, (device, delivery) in enumerate(pending):
        ticket = tickets[index] if index < len(tickets) else {}
        ticket_error = ticket.get('message') or ticket.get('details', {}).get('error', '')
        delivery.expo_ticket_id = str(ticket.get('id') or '')[:255]
        delivery.error = str(ticket_error)[:500]
        delivery.status = 'sent' if ticket.get('status') == 'ok' else 'failed'
        delivery.sent_at = timezone.now() if delivery.status == 'sent' else None
        delivery.save(update_fields=['expo_ticket_id', 'error', 'status', 'sent_at'])
        if ticket.get('details', {}).get('error') == 'DeviceNotRegistered':
            now = timezone.now()
            device.enabled = False; device.invalidated_at = now; device.disabled_at = now
            device.save(update_fields=['enabled', 'invalidated_at', 'disabled_at', 'updated_at'])
            delivery.status = 'invalid'; delivery.save(update_fields=['status']); result['removed'] += 1
        elif delivery.status == 'sent':
            result['sent'] += 1
        else:
            result['failed'] += 1
    return result
