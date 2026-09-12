from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('league', '0019_userprofile_native_push_prompt_seen_at')]

    operations = [
        migrations.AddField(model_name='userprofile', name='native_notifications_enabled', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='nativepushdevice', name='installation_id', field=models.CharField(blank=True, max_length=128, null=True, unique=True)),
        migrations.AddField(model_name='nativepushdevice', name='registered_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='nativepushdevice', name='disabled_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='nativepushdevice', name='invalidated_at', field=models.DateTimeField(blank=True, null=True)),
    ]
