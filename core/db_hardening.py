"""PostgreSQL triggers that make history tables append-only.

Run via `python manage.py harden_db` after every `migrate` (the installer does this).
Add table names here as later phases introduce more history tables (sales, stock
movements, refunds ...).
"""
APPEND_ONLY_TABLES = [
    "core_auditlog",
    "accounts_loginattempt",
    "inventory_stockmovement",
    "catalog_shelftransfer",
]

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION winespos_block_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Table % is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


# Completed or voided sales are history. Their rows (and their lines and payments) cannot be changed
# or deleted; only the receipt-print counters on the sale itself may still move.
FINAL_SALE_SQL = """
CREATE OR REPLACE FUNCTION winespos_protect_final_sale() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('COMPLETED', 'VOIDED') THEN
            RAISE EXCEPTION 'A completed or voided sale cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status IN ('COMPLETED', 'VOIDED') THEN
        IF (to_jsonb(NEW) - 'reprint_count' - 'printed_at') IS DISTINCT FROM
           (to_jsonb(OLD) - 'reprint_count' - 'printed_at') THEN
            RAISE EXCEPTION 'A completed or voided sale cannot be modified';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION winespos_protect_final_sale_child() RETURNS trigger AS $$
DECLARE
    sid bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN sid := NEW.sale_id; ELSE sid := OLD.sale_id; END IF;
    IF EXISTS (SELECT 1 FROM sales_sale WHERE id = sid AND status IN ('COMPLETED', 'VOIDED')) THEN
        RAISE EXCEPTION 'Lines and payments of a completed or voided sale cannot be changed';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
FINAL_SALE_CHILD_TABLES = ["sales_saleitem", "sales_payment"]


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
        cursor.execute(FINAL_SALE_SQL)
        cursor.execute('DROP TRIGGER IF EXISTS winespos_final_sale ON "sales_sale"')
        cursor.execute('CREATE TRIGGER winespos_final_sale BEFORE UPDATE OR DELETE ON "sales_sale" '
                       "FOR EACH ROW EXECUTE FUNCTION winespos_protect_final_sale()")
        for table in FINAL_SALE_CHILD_TABLES:
            cursor.execute(f'DROP TRIGGER IF EXISTS winespos_final_sale ON "{table}"')
            cursor.execute(f'CREATE TRIGGER winespos_final_sale BEFORE INSERT OR UPDATE OR DELETE ON "{table}" '
                           "FOR EACH ROW EXECUTE FUNCTION winespos_protect_final_sale_child()")
    return list(APPEND_ONLY_TABLES) + ["sales_sale (completed rows)"] + FINAL_SALE_CHILD_TABLES
