from django.db import migrations, models
import django.db.models.deletion


def create_profiles(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('league', 'UserProfile')
    UserProfile.objects.bulk_create([UserProfile(user_id=user_id) for user_id in User.objects.values_list('id', flat=True)], ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [('league', '0004_spotifyconnection')]
    operations = [
        migrations.CreateModel(
            name='UserProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('picture', models.ImageField(blank=True, upload_to='profile_pictures/%Y/%m/')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='profile', to='auth.user')),
            ],
        ),
        migrations.RunPython(create_profiles, migrations.RunPython.noop),
    ]
