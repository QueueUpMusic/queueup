from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('league', '0002_delete_spotifyprofile')]
    operations = [
        migrations.DeleteModel(name='Comment'),
        migrations.RemoveField(model_name='round', name='playlist_id'),
        migrations.AddField(model_name='round', name='reveal_notification_sent', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='round', name='submission_notification_sent', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='round', name='voting_notification_sent', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='submission', name='artist_ids', field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name='submission', name='genres', field=models.JSONField(blank=True, default=list)),
        migrations.CreateModel(
            name='PushSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('endpoint', models.TextField(unique=True)),
                ('p256dh', models.TextField()),
                ('auth', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='push_subscriptions', to='auth.user')),
            ],
        ),
    ]
