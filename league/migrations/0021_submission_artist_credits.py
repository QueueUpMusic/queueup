from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('league', '0020_native_push_installation_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='submission',
            name='artist_credits',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Ordered Spotify artist credits: [{"id": ..., "name": ...}].',
            ),
        ),
    ]
