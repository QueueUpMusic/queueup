from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_badges(apps, schema_editor):
    Badge = apps.get_model('league', 'Badge')
    rows = [
        ('long-haul', 'Long Haul', 'Play 25 rounds', '🛣️', 'twenty_five_rounds', False, 20),
        ('queueup-legend', 'QueueUp Legend', 'Play 50 rounds', '👑', 'fifty_rounds', True, 10),
        ('podium-fixture', 'Podium Fixture', 'Earn ten podium finishes', '🥇', 'podium_ten', True, 30),
        ('five-time-champion', 'Five-Time Champion', 'Win five rounds', '🏅', 'five_wins', True, 15),
        ('dynasty', 'Dynasty', 'Win ten rounds', '⚜️', 'ten_wins', True, 5),
        ('seasoned', 'Seasoned', 'Compete in three different seasons', '🌟', 'three_seasons', True, 25),
        ('founding-myth', 'Founding Myth', 'Compete in five different seasons', '🗿', 'five_seasons', True, 1),
        ('genre-hopper', 'Genre Hopper', 'Submit songs spanning ten genres', '🌈', 'genre_hopper', True, 35),
        ('photo-finish', 'Photo Finish', 'Finish tied for first place', '📸', 'close_call', True, 40),
    ]
    for slug, name, description, icon, key, hidden, order in rows:
        Badge.objects.get_or_create(slug=slug, defaults={
            'name': name, 'description': description, 'icon': icon,
            'achievement_key': key, 'hidden': hidden,
            'display_next_to_name': True, 'active': True, 'sort_order': order,
        })


class Migration(migrations.Migration):
    dependencies = [('league', '0007_notificationdelivery')]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    ALTER TABLE league_season ADD COLUMN IF NOT EXISTS banner varchar(100);
                    UPDATE league_season SET banner = '' WHERE banner IS NULL;
                    ALTER TABLE league_season ALTER COLUMN banner SET NOT NULL;
                    ALTER TABLE league_season ADD COLUMN IF NOT EXISTS description text;
                    UPDATE league_season SET description = '' WHERE description IS NULL;
                    ALTER TABLE league_season ALTER COLUMN description SET NOT NULL;
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='season',
                    name='banner',
                    field=models.ImageField(blank=True, upload_to='season_banners/%Y/%m/'),
                ),
                migrations.AddField(
                    model_name='season',
                    name='description',
                    field=models.TextField(blank=True, help_text='Shown in the new-season welcome.'),
                ),
            ],
        ),
        migrations.CreateModel(
            name='Badge',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)), ('slug', models.SlugField(max_length=80, unique=True)),
                ('description', models.CharField(max_length=240)), ('icon', models.CharField(default='◆', help_text='Emoji or short symbol.', max_length=16)),
                ('achievement_key', models.CharField(blank=True, help_text='Optional automatic achievement key.', max_length=80)),
                ('hidden', models.BooleanField(default=False)), ('display_next_to_name', models.BooleanField(default=True)),
                ('active', models.BooleanField(default=True)), ('sort_order', models.PositiveIntegerField(default=100)),
            ], options={'ordering': ['sort_order', 'name']},
        ),
        migrations.CreateModel(
            name='SeasonWelcome',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('seen_at', models.DateTimeField(auto_now_add=True)),
                ('season', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='welcomed_users', to='league.season')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='season_welcomes', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='UserBadge',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('awarded_at', models.DateTimeField(auto_now_add=True)),
                ('awarded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='badges_given', to=settings.AUTH_USER_MODEL)),
                ('badge', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='awards', to='league.badge')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='badge_awards', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(model_name='seasonwelcome', constraint=models.UniqueConstraint(fields=('user', 'season'), name='one_welcome_per_user_season')),
        migrations.AddConstraint(model_name='userbadge', constraint=models.UniqueConstraint(fields=('user', 'badge'), name='one_badge_award_per_user')),
        migrations.RunPython(seed_badges, migrations.RunPython.noop),
    ]
