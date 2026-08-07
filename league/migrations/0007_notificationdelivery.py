from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('league', '0006_notification_expansion')]
    operations = [
        migrations.CreateModel(
            name='NotificationDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_key', models.CharField(max_length=200)),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='league.pushsubscription')),
            ],
        ),
        migrations.AddConstraint(
            model_name='notificationdelivery',
            constraint=models.UniqueConstraint(fields=('subscription', 'event_key'), name='one_push_delivery_per_event'),
        ),
    ]
