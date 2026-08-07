from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('league', '0003_product_upgrade'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name='SpotifyConnection',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('spotify_user_id', models.CharField(blank=True, max_length=128)),
            ('display_name', models.CharField(blank=True, max_length=200)),
            ('access_token', models.TextField()), ('refresh_token', models.TextField()),
            ('expires_at', models.DateTimeField()), ('scope', models.TextField(blank=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='spotify_connection', to=settings.AUTH_USER_MODEL)),
        ],
    )]
