from django.db import migrations


def reset_genre_hopper_unlocks(apps, schema_editor):
    # Older releases could award this from multiple subgenre tags on only one
    # or two songs. Clear those stored unlock markers so the corrected dynamic
    # achievement calculation can award it again only when genuinely earned.
    AchievementUnlock = apps.get_model('league', 'AchievementUnlock')
    AchievementUnlock.objects.filter(key='genre_hopper').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('league', '0010_round_archived'),
    ]

    operations = [
        migrations.RunPython(reset_genre_hopper_unlocks, migrations.RunPython.noop),
    ]
