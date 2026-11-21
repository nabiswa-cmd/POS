from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tills", "0007_cashiershift_unrestricted_selling"),
    ]

    operations = [
        migrations.AddField(
            model_name="till",
            name="local_server_urls",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
