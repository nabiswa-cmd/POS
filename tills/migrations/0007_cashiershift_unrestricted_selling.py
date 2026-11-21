from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tills", "0006_cashiershift_locked_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="cashiershift",
            name="unrestricted_selling",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="cashiershift",
            name="unrestricted_authorized_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="unrestricted_shifts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="cashiershift",
            name="unrestricted_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
