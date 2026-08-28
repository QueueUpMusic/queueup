from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('league', '0014_submission_isrc_unique'),
    ]

    operations = [
        migrations.CreateModel(
            name='SeasonRecapView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('viewed_at', models.DateTimeField(auto_now_add=True)),
                ('season', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recap_views', to='league.season')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='season_recap_views', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='seasonrecapview',
            constraint=models.UniqueConstraint(fields=('user', 'season'), name='one_recap_view_per_user_season'),
        ),
    ]
