from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('league', '0017_notificationblast_sending_started_at')]
    operations = [
        migrations.CreateModel(
            name='NativePushDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('expo_push_token', models.CharField(max_length=255, unique=True)),
                ('platform', models.CharField(choices=[('ios', 'iOS'), ('android', 'Android')], max_length=8)),
                ('device_id', models.CharField(blank=True, max_length=255)),
                ('app_version', models.CharField(blank=True, max_length=64)),
                ('enabled', models.BooleanField(default=True)),
                ('last_seen_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='native_push_devices', to='auth.user')),
            ],
            options={'indexes': [models.Index(fields=['user', 'enabled'], name='league_nati_user_id_8f3f5c_idx')]},
        ),
        migrations.CreateModel(
            name='NativeNotificationDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_key', models.CharField(max_length=200)), ('title', models.CharField(max_length=160)),
                ('body', models.TextField(max_length=1000)), ('expo_ticket_id', models.CharField(blank=True, max_length=255)),
                ('status', models.CharField(default='pending', max_length=32)), ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='league.nativepushdevice')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='native_notification_deliveries', to='auth.user')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('device', 'event_key'), name='one_native_delivery_per_event')]},
        ),
    ]
