from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('league', '0015_seasonrecapview')]

    operations = [
        migrations.CreateModel(
            name='HomepageCountdown',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=160)),
                ('target_at', models.DateTimeField()),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_countdowns', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-updated_at', '-pk']},
        ),
        migrations.CreateModel(
            name='NotificationBlast',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=120)),
                ('body', models.TextField(max_length=1000)),
                ('destination', models.CharField(default='/home/', max_length=500)),
                ('audience', models.CharField(choices=[('approved', 'All approved players')], default='approved', max_length=24)),
                ('scheduled_for', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('scheduled', 'Scheduled'), ('sending', 'Sending'), ('sent', 'Sent'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_notification_blasts', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at', '-pk']},
        ),
    ]
