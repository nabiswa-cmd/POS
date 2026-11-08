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
    "sales_mpesacallbacklog",      # exactly what Safaricom sent, never edited or deleted
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

# Refunds are history too. A refund is never deleted; once PAID, REJECTED or CANCELLED nothing but its print
# counter may change; and the sale, amount, method and number of any refund never change. Refund lines can only
# have their resaleable flags set while the refund is still open (that is when stock is returned).
# A KRA-signed document is evidence: never deleted, and once SUBMITTED not changed (sales invoices and credit notes).
SIGNED_SQL = """
CREATE OR REPLACE FUNCTION winespos_protect_signed() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'eTIMS records cannot be deleted';
    END IF;
    IF OLD.status = 'SUBMITTED' THEN
        RAISE EXCEPTION 'A signed eTIMS document cannot be modified';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
SIGNED_TABLES = ["sales_etimssubmission", "sales_etimscreditnote"]

# A Safaricom payment is money evidence: never deleted; its receipt code, amount and source never change; and once it has
# been used on a sale it cannot be un-used or moved to another sale. (Customer name and phone may still be filled in, and
# a waiting payment may be set aside / put back: that is reconciliation.)
MPESA_SQL = """
CREATE OR REPLACE FUNCTION winespos_protect_mpesa() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'M-PESA payment records cannot be deleted';
    END IF;
    IF NEW.trans_id <> OLD.trans_id OR NEW.amount <> OLD.amount OR NEW.source <> OLD.source THEN
        RAISE EXCEPTION 'An M-PESA receipt code, amount and source cannot be changed';
    END IF;
    IF OLD.status = 'MATCHED' AND (NEW.status <> OLD.status OR NEW.sale_id IS DISTINCT FROM OLD.sale_id) THEN
        RAISE EXCEPTION 'An M-PESA payment already used on a sale cannot be moved or released';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

REFUND_SQL = """
CREATE OR REPLACE FUNCTION winespos_protect_refund() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Refunds cannot be deleted';
    END IF;
    IF NEW.sale_id <> OLD.sale_id OR NEW.total <> OLD.total OR NEW.method <> OLD.method OR NEW.number <> OLD.number THEN
        RAISE EXCEPTION 'The sale, amount, method and number of a refund cannot be changed';
    END IF;
    IF OLD.status IN ('PAID', 'REJECTED', 'CANCELLED') THEN
        IF (to_jsonb(NEW) - 'print_count') IS DISTINCT FROM (to_jsonb(OLD) - 'print_count') THEN
            RAISE EXCEPTION 'A finished refund cannot be modified';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION winespos_protect_refund_item() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Refund lines cannot be deleted';
    END IF;
    IF (to_jsonb(NEW) - 'restock' - 'restocked') IS DISTINCT FROM (to_jsonb(OLD) - 'restock' - 'restocked') THEN
        RAISE EXCEPTION 'Refund lines cannot be changed';
    END IF;
    IF (NEW.restock <> OLD.restock OR NEW.restocked <> OLD.restocked) AND
       EXISTS (SELECT 1 FROM sales_refund WHERE id = OLD.refund_id AND status IN ('PAID', 'REJECTED', 'CANCELLED')) THEN
        RAISE EXCEPTION 'Refund lines of a finished refund cannot be changed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# A cash-up is the record of a counted drawer: never deleted, and the only thing that may change is the one-time
# "superseded" mark written when a shift is reopened (after that the row is frozen). An expense is never deleted and
# only the one-time void mark may be written.
CASHUP_SQL = """
CREATE OR REPLACE FUNCTION winespos_protect_cashup() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Cash-ups cannot be deleted';
    END IF;
    IF OLD.superseded_at IS NOT NULL THEN
        RAISE EXCEPTION 'A superseded cash-up cannot be modified';
    END IF;
    IF (to_jsonb(NEW) - 'superseded_at' - 'superseded_by_id' - 'supersede_reason') IS DISTINCT FROM
       (to_jsonb(OLD) - 'superseded_at' - 'superseded_by_id' - 'supersede_reason') THEN
        RAISE EXCEPTION 'A cash-up cannot be edited';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

EXPENSE_SQL = """
CREATE OR REPLACE FUNCTION winespos_protect_expense() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Expenses cannot be deleted. Void the entry instead';
    END IF;
    IF OLD.voided_at IS NOT NULL THEN
        RAISE EXCEPTION 'A voided expense cannot be modified';
    END IF;
    IF (to_jsonb(NEW) - 'voided_at' - 'voided_by_id' - 'void_reason') IS DISTINCT FROM
       (to_jsonb(OLD) - 'voided_at' - 'voided_by_id' - 'void_reason') THEN
        RAISE EXCEPTION 'An expense cannot be edited. Void it and enter it again';
    END IF;
    RETURN NEW;
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
        cursor.execute(FINAL_SALE_SQL)
        cursor.execute('DROP TRIGGER IF EXISTS winespos_final_sale ON "sales_sale"')
        cursor.execute('CREATE TRIGGER winespos_final_sale BEFORE UPDATE OR DELETE ON "sales_sale" '
                       "FOR EACH ROW EXECUTE FUNCTION winespos_protect_final_sale()")
        for table in FINAL_SALE_CHILD_TABLES:
            cursor.execute(f'DROP TRIGGER IF EXISTS winespos_final_sale ON "{table}"')
            cursor.execute(f'CREATE TRIGGER winespos_final_sale BEFORE INSERT OR UPDATE OR DELETE ON "{table}" '
                           "FOR EACH ROW EXECUTE FUNCTION winespos_protect_final_sale_child()")
        cursor.execute(REFUND_SQL)
        cursor.execute('DROP TRIGGER IF EXISTS winespos_refund ON "sales_refund"')
        cursor.execute('CREATE TRIGGER winespos_refund BEFORE UPDATE OR DELETE ON "sales_refund" '
                       "FOR EACH ROW EXECUTE FUNCTION winespos_protect_refund()")
        cursor.execute('DROP TRIGGER IF EXISTS winespos_refund_item ON "sales_refunditem"')
        cursor.execute('CREATE TRIGGER winespos_refund_item BEFORE UPDATE OR DELETE ON "sales_refunditem" '
                       "FOR EACH ROW EXECUTE FUNCTION winespos_protect_refund_item()")
        cursor.execute(MPESA_SQL)
        cursor.execute('DROP TRIGGER IF EXISTS winespos_mpesa ON "sales_mpesatransaction"')
        cursor.execute('CREATE TRIGGER winespos_mpesa BEFORE UPDATE OR DELETE ON "sales_mpesatransaction" '
                       "FOR EACH ROW EXECUTE FUNCTION winespos_protect_mpesa()")
        cursor.execute(SIGNED_SQL)
        for table in SIGNED_TABLES:
            cursor.execute(f'DROP TRIGGER IF EXISTS winespos_signed ON "{table}"')
            cursor.execute(f'CREATE TRIGGER winespos_signed BEFORE UPDATE OR DELETE ON "{table}" '
                           "FOR EACH ROW EXECUTE FUNCTION winespos_protect_signed()")
        cursor.execute(CASHUP_SQL)
        cursor.execute('DROP TRIGGER IF EXISTS winespos_cashup ON "tills_cashup"')
        cursor.execute('CREATE TRIGGER winespos_cashup BEFORE UPDATE OR DELETE ON "tills_cashup" '
                       "FOR EACH ROW EXECUTE FUNCTION winespos_protect_cashup()")
        cursor.execute(EXPENSE_SQL)
        cursor.execute('DROP TRIGGER IF EXISTS winespos_expense ON "accounting_expense"')
        cursor.execute('CREATE TRIGGER winespos_expense BEFORE UPDATE OR DELETE ON "accounting_expense" '
                       "FOR EACH ROW EXECUTE FUNCTION winespos_protect_expense()")
    return (list(APPEND_ONLY_TABLES) + ["sales_sale (completed rows)"] + FINAL_SALE_CHILD_TABLES
            + ["sales_refund (never deleted; finished rows frozen)", "sales_refunditem"]
            + [f"{t} (signed rows frozen)" for t in SIGNED_TABLES]
            + ["tills_cashup (never deleted; only the reopen mark can be written)",
               "accounting_expense (never deleted; only the void mark can be written)"]
            + ["sales_mpesatransaction (never deleted; code and amount frozen; used payments locked)"])
