import json

from django.conf import settings
from django.db import IntegrityError, transaction
from pywebpush import WebPushException, webpush

from .models import NotificationDelivery, PushSubscription


def send_push(subscriptions, event_key, title, body, url='/home/', stderr=None):
    totals = {'sent': 0, 'skipped': 0, 'removed': 0, 'failed': 0}
    if not settings.WEBPUSH_PRIVATE_KEY or not settings.WEBPUSH_PUBLIC_KEY:
        return totals
    payload = json.dumps({'title': title, 'body': body, 'url': url, 'tag': event_key})
    for subscription in subscriptions.iterator():
        try:
            with transaction.atomic():
                _, created = NotificationDelivery.objects.get_or_create(subscription=subscription, event_key=event_key)
                if not created:
                    totals['skipped'] += 1
                    continue
                try:
                    webpush(
                        subscription_info={'endpoint': subscription.endpoint, 'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth}},
                        data=payload,
                        vapid_private_key=settings.WEBPUSH_PRIVATE_KEY,
                        vapid_claims={'sub': settings.WEBPUSH_CONTACT},
                    )
                except Exception:
                    NotificationDelivery.objects.filter(subscription=subscription, event_key=event_key).delete()
                    raise
                totals['sent'] += 1
        except IntegrityError:
            totals['skipped'] += 1
        except WebPushException as exc:
            status = getattr(exc.response, 'status_code', None)
            if status in (404, 410):
                subscription.delete()
                totals['removed'] += 1
            else:
                totals['failed'] += 1
                if stderr:
                    stderr.write(f'Push failed for subscription {subscription.pk}: {exc}')
        except Exception as exc:
            totals['failed'] += 1
            if stderr:
                stderr.write(f'Push failed for subscription {subscription.pk}: {exc}')
    return totals


def send_user_push(user, event_key, title, body, url='/home/', stderr=None):
    return send_push(PushSubscription.objects.filter(user=user), event_key, title, body, url, stderr)
