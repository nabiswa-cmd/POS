from django.db import migrations


def repair_printer_name_column(apps, schema_editor):
    model = apps.get_model("core", "ReceiptSettings")
    table = model._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        tables = schema_editor.connection.introspection.table_names(cursor)
        if table not in tables:
            raise RuntimeError(f"Cannot repair missing {table}: the table itself does not exist.")
        columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(cursor, table)
        }
    if "printer_name" not in columns:
        schema_editor.add_field(model, model._meta.get_field("printer_name"))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_receiptsettings_printer_name"),
    ]

    operations = [
        migrations.RunPython(repair_printer_name_column, migrations.RunPython.noop),
    ]
