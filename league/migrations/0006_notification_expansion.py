from django.db import migrations, models
import django.db.models.deletion


def baseline_existing_achievements(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Submission = apps.get_model('league', 'Submission')
    Vote = apps.get_model('league', 'Vote')
    AchievementUnlock = apps.get_model('league', 'AchievementUnlock')
    from django.db.models import Avg

    for user in User.objects.all():
        subs = Submission.objects.filter(user=user)
        keys = []
        count = subs.count()
        if count >= 1: keys.append('first_pick')
        if count >= 5: keys.append('five_rounds')
        if count >= 10: keys.append('ten_rounds')
        if subs.values('artist').distinct().count() >= 10: keys.append('deep_cut')
        if subs.annotate(avg=Avg('votes__score')).filter(avg__gte=5).exists(): keys.append('perfect_score')

        wins = podiums = 0
        for sub in subs.select_related('round'):
            ranked = list(Submission.objects.filter(round=sub.round).annotate(avg=Avg('votes__score')).order_by('-avg', 'submitted_at').values_list('id', flat=True))
            if sub.id in ranked and Vote.objects.filter(submission__round=sub.round).exists():
                place = ranked.index(sub.id) + 1
                wins += place == 1
                podiums += place <= 3
        if wins >= 1: keys.append('first_win')
        if podiums >= 3: keys.append('taste_maker')
        for key in keys:
            AchievementUnlock.objects.get_or_create(user=user, key=key)


class Migration(migrations.Migration):
    dependencies = [('league', '0005_userprofile')]
    operations = [
        migrations.AddField(model_name='round', name='announcement_notification_sent', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='round', name='submission_reminder_notification_sent', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='round', name='voting_reminder_notification_sent', field=models.BooleanField(default=False)),
        migrations.CreateModel(
            name='AchievementUnlock',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=80)),
                ('earned_at', models.DateTimeField(auto_now_add=True)),
                ('notification_sent_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='achievement_unlocks', to='auth.user')),
            ],
        ),
        migrations.AddConstraint(model_name='achievementunlock', constraint=models.UniqueConstraint(fields=('user', 'key'), name='one_unlock_per_user_achievement')),
        migrations.RunPython(baseline_existing_achievements, migrations.RunPython.noop),
    ]
