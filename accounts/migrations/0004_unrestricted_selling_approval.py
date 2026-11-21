from django.db import migrations, models


def add_unrestricted_selling_approval(apps, schema_editor):
    ApprovalPolicy = apps.get_model("accounts", "ApprovalPolicy")
    ApprovalPolicy.objects.get_or_create(action="unrestricted_selling", defaults={"required": True})
    Role = apps.get_model("accounts", "Role")
    RolePermission = apps.get_model("accounts", "RolePermission")
    supervisor = Role.objects.filter(name="Supervisor", is_system=True).first()
    if supervisor is not None:
        RolePermission.objects.get_or_create(role=supervisor, code="approve.unrestricted_selling")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_alter_approval_reason"),
    ]

    operations = [
        migrations.AlterField(
            model_name="approvalpolicy",
            name="action",
            field=models.CharField(
                choices=[
                    ("open_pos", "Open POS session"),
                    ("remove_item", "Remove scanned item from sale"),
                    ("void_sale", "Void a transaction"),
                    ("price_change", "Change price during checkout"),
                    ("exceptional_discount", "Exceptional discount"),
                    ("refund", "Process a refund"),
                    ("reopen_shift", "Reopen a closed shift"),
                    ("stock_adjust", "Adjust stock"),
                    ("negative_stock_sale", "Sell below available stock"),
                    ("unrestricted_selling", "Temporarily enable unrestricted selling"),
                ],
                max_length=40,
                unique=True,
            ),
        ),
        migrations.RunPython(add_unrestricted_selling_approval, migrations.RunPython.noop),
    ]
