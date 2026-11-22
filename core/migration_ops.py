from django.db import migrations


class AddFieldIfMissing(migrations.AddField):
    """Make an initial field addition safe when a restored schema is ahead of its migration record."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        table = model._meta.db_table
        with schema_editor.connection.cursor() as cursor:
            if table not in schema_editor.connection.introspection.table_names(cursor):
                return super().database_forwards(app_label, schema_editor, from_state, to_state)
            columns = {
                column.name
                for column in schema_editor.connection.introspection.get_table_description(cursor, table)
            }
        field = model._meta.get_field(self.name)
        if field.column in columns:
            return
        return super().database_forwards(app_label, schema_editor, from_state, to_state)
