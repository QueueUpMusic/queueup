import uuid

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from ...services.native_push import send_native_push


class Command(BaseCommand):
    help = 'Send a development-only native push test to an active user installation.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--all-devices', action='store_true')

    def handle(self, *args, **options):
        username = options['username']
        try:
            user = User.objects.get(username=username, is_active=True)
        except User.DoesNotExist as exc:
            raise CommandError(f'Active user not found: {username}') from exc

        devices = user.native_push_devices.filter(
            enabled=True, invalidated_at__isnull=True, registered_at__isnull=False,
        ).order_by('-last_seen_at', '-pk')
        if not options['all_devices']:
            devices = devices[:1]
        device_ids = list(devices.values_list('pk', flat=True))
        if not device_ids:
            raise CommandError('No active native push devices are registered for this user.')

        event_key = f'native-test:{uuid.uuid4().hex}'
        result = send_native_push(
            user, event_key,
            '[TEST] QueueUp notifications', 'Native push delivery is working.',
            '/home/', event_at=None, stderr=self.stderr, device_ids=device_ids,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Native test complete: {result['sent']} sent, "
            f"{result['failed']} failed, {result['skipped']} skipped, "
            f"{result['removed']} invalidated."
        ))
