"""PostgreSQL triggers that make history tables append-only.

Run via `python manage.py harden_db` after every `migrate` (the installer does this).
Add table names here as later phases introduce more history tables (sales, stock
movements, refunds ...).
"""
APPEND_ONLY_TABLES = [
    "core_auditlog",
    "accounts_loginattempt",
]

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION winespos_block_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Table % is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


def apply(connection):
    """Install triggers. Returns the list of tables protected (empty if unsupported)."""
    if connection.vendor != "postgresql":
        return []
    with connection.cursor() as cursor:
        cursor.execute(FUNCTION_SQL)
        for table in APPEND_ONLY_TABLES:
            cursor.execute(f'DROP TRIGGER IF EXISTS winespos_append_only ON "{table}"')
            cursor.execute(
                f'CREATE TRIGGER winespos_append_only BEFORE UPDATE OR DELETE ON "{table}" '
                "FOR EACH ROW EXECUTE FUNCTION winespos_block_change()"
            )
    return list(APPEND_ONLY_TABLES)
