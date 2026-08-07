from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('league', '0009_v70_membership_rounds_clean_music'),
    ]

    operations = [
        migrations.AddField(
            model_name='round',
            name='archived',
            field=models.BooleanField(default=False, help_text='Hide this completed round from the homepage while keeping it in the archive.'),
        ),
    ]
