import re

from django.utils import timezone

from ..models import NativeNotificationDelivery, NativePushDevice

EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'
TOKEN_RE = re.compile(r'^(?:Expo|Exponent)PushToken\[[^\]]+\]$')


class InvalidNativePushDevice(Exception):
    pass


def register_device(user, data):
    token = data.get('expo_push_token')
    platform = data.get('platform')
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise InvalidNativePushDevice('A valid Expo push token is required.')
    if platform not in NativePushDevice.Platform.values:
        raise InvalidNativePushDevice('Platform must be ios or android.')
    device = NativePushDevice.objects.filter(expo_push_token=token).first()
    if device:
        device.user = user
        device.platform = platform
        device.device_id = str(data.get('device_id') or '')[:255]
        device.app_version = str(data.get('app_version') or '')[:64]
        device.enabled = True
        device.last_seen_at = timezone.now()
        device.save(update_fields=['user', 'platform', 'device_id', 'app_version', 'enabled', 'last_seen_at', 'updated_at'])
        return device
    return NativePushDevice.objects.create(user=user, expo_push_token=token, platform=platform, device_id=str(data.get('device_id') or '')[:255], app_version=str(data.get('app_version') or '')[:64], last_seen_at=timezone.now())


def unregister_device(user, token):
    if not isinstance(token, str) or not token:
        return False
    deleted, _ = NativePushDevice.objects.filter(user=user, expo_push_token=token).delete()
    return bool(deleted)


def device_registered(user, token):
    if not isinstance(token, str) or not token:
        return False
    return NativePushDevice.objects.filter(user=user, expo_push_token=token, enabled=True).exists()


def send_native_push(users, event_key, title, body, route='/home/', *, stderr=None):
    if hasattr(users, 'pk'):
        users = [users]
    devices = NativePushDevice.objects.filter(user__in=users, user__is_active=True, enabled=True).select_related('user')
    result = {'sent': 0, 'skipped': 0, 'failed': 0, 'removed': 0}
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
        if stderr: stderr.write(f'Native push transport failed: {str(exc)[:200]}\n')
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
            device.enabled = False; device.save(update_fields=['enabled', 'updated_at'])
            delivery.status = 'invalid'; delivery.save(update_fields=['status']); result['removed'] += 1
        elif delivery.status == 'sent': result['sent'] += 1
        else: result['failed'] += 1
    return result
