from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0006_reconciliation'),
    ]

    operations = [
        migrations.CreateModel(
            name='MpesaCallbackLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('c2b', 'Till / paybill confirmation'), ('stk', 'STK push result')], max_length=8)),
                ('received_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('source_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('payload', models.JSONField()),
                ('outcome', models.CharField(blank=True, help_text="'ok' when it was accepted and applied here.", max_length=200)),
            ],
            options={
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='MpesaRelayCursor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_id', models.PositiveBigIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
