"""Builds the data for a receipt from a completed sale, then lays it out with receipt_text.

Names printed: the cashier who made the sale, and (under "Authorized by") the supervisor or manager who
authorized the POS session. A supervisor who only authorized the session is never printed as the seller.
Anyone who approved a discount or price change on a line is listed in a note under the totals.
"""
from django.utils import timezone

from core.models import ReceiptSettings, ShopSettings

from . import etims, receipt_text, tax
from .models import Sale


def build_receipt(sale, *, duplicate=False):
    shop, cfg = ShopSettings.load(), ReceiptSettings.load()
    items = list(sale.items.all())
    notes = []
    for who in sorted({i.discount_approved_by.display_name for i in items if i.discount_approved_by_id}):
        notes.append(f"Discount approved by {who}")
    for who in sorted({i.price_approved_by.display_name for i in items if i.price_approved_by_id}):
        notes.append(f"Price change approved by {who}")
    header = [shop.shop_name] + [x for x in (cfg.header_text, shop.address, shop.phone,
                                             f"KRA PIN: {shop.kra_pin}" if shop.kra_pin else "") if x]
    vat = None
    if sale.vat_applied:
        by_class, taxable_total, vat_total = tax.summarize((i.tax_class, i.tax_rate, i.line_total) for i in items)
        vat = {"rows": [{"class": k, "rate": str(v["rate"]), "taxable": str(v["taxable"]), "tax": str(v["tax"])}
                        for k, v in by_class.items()],
               "taxable_total": str(taxable_total), "tax_total": str(vat_total)}
    sub = getattr(sale, "etims", None) if hasattr(sale, "etims") else None
    etims_block = None
    if etims.enabled() or sub is not None:
        etims_block = {"status": sub.status if sub else "PENDING", "invoice_no": sub.invoice_no if sub else "",
                       "scu_id": sub.scu_id if sub else "", "scu_receipt_no": sub.scu_receipt_no if sub else "",
                       "scu_datetime": sub.scu_datetime if sub else "", "internal_data": sub.internal_data if sub else "",
                       "signature": sub.signature if sub else "", "qr_text": sub.qr_text if sub else ""}
    return {
        "sale_id": sale.pk,
        "number": sale.number,
        "date": timezone.localtime(sale.completed_at).strftime("%Y-%m-%d %H:%M"),
        "till": sale.till.name,
        "customer": sale.customer_name,
        "cashier": sale.cashier.display_name if cfg.show_cashier else "",
        # An owner who opened their own till is not printed as "authorized by" themselves.
        "authorized_by": (sale.authorized_by.display_name
                          if cfg.show_authorized_by and sale.authorized_by_id != sale.cashier_id else ""),
        "customer_pin": sale.customer_pin,
        "item_tax_codes": bool(sale.vat_applied),
        "header_lines": header,
        "items": [{"name": (f"{i.product_name} ({i.selling_unit_name})" if i.is_partial else
                            f"{i.product_name} ({i.pack_name})" if i.pack_name else i.product_name),
                   "quantity": i.quantity, "unit_price": str(i.unit_price), "gross": str(i.gross), "tax_class": i.tax_class,
                   "discount": str(i.discount), "line_total": str(i.line_total)} for i in items],
        "subtotal": str(sale.subtotal), "discount": str(sale.discount_total), "total": str(sale.total),
        "payments": [{"method": p.get_method_display().upper(), "amount": str(p.amount),
                      "tendered": str(p.tendered) if p.tendered is not None else None,
                      "change": str(p.change_given), "reference": p.reference}
                     for p in sale.payments.all()],
        "vat": vat,
        "etims": etims_block,
        "approval_notes": notes,
        "footer": cfg.footer_text,
        "duplicate": duplicate,
        "reprint_number": sale.reprint_count if duplicate else 0,
        "paper_mm": cfg.paper_width_mm,
    }


def render(sale, *, duplicate=False):
    data = build_receipt(sale, duplicate=duplicate)
    return data, receipt_text.format_receipt(data, data["paper_mm"])


def get_completed(sale_id):
    return (Sale.objects.select_related("till", "cashier", "authorized_by", "etims")
            .prefetch_related("items__discount_approved_by", "items__price_approved_by", "payments")
            .filter(pk=sale_id, status=Sale.COMPLETED).first())
