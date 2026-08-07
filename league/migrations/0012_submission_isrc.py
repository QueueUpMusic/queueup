from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('league', '0011_reset_genre_hopper_unlocks'),
    ]

    operations = [
        migrations.AddField(
            model_name='submission',
            name='isrc',
            field=models.CharField(blank=True, db_index=True, max_length=32, null=True),
        ),
    ]
