from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0011_saleitem_full_volume_per_sale_unit_ml_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReceiptPrintJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("ORIGINAL", "Original"), ("DUPLICATE", "Duplicate")], max_length=9)),
                ("payload", models.JSONField()),
                ("status", models.CharField(choices=[("QUEUED", "Queued"), ("PRINTING", "Printing"), ("PRINTED", "Printed"), ("FAILED", "Failed")], db_index=True, default="QUEUED", max_length=8)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("printed_at", models.DateTimeField(blank=True, null=True)),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="receipt_print_jobs", to=settings.AUTH_USER_MODEL)),
                ("sale", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="receipt_print_jobs", to="sales.sale")),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
    ]
