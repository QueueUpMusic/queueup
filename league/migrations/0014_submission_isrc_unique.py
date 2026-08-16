from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('league', '0013_round_is_draft'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='submission',
            name='no_duplicate_track_per_round',
        ),
        migrations.AddConstraint(
            model_name='submission',
            constraint=models.UniqueConstraint(
                condition=models.Q(isrc__isnull=False),
                fields=('round', 'isrc'),
                name='no_duplicate_isrc_per_round',
            ),
        ),
    ]
