from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('league', '0018_nativepushdevice_nativenotificationdelivery')]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='native_push_prompt_seen_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
