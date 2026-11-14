"""Operational sales, refund, inventory and audit reports."""
from decimal import Decimal

from django.db.models import (Case, Count, DecimalField, F, IntegerField, OuterRef, Q, Subquery, Sum, Value,
                              When)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from catalog.models import Product
from core.models import AuditLog
from inventory import movement_rules as mr
from inventory.models import Bottle, StockMovement

from .models import Refund, RefundItem, Sale, SaleItem

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=14, decimal_places=2)
VOLUME = DecimalField(max_digits=14, decimal_places=2)
VOLUME_LEDGER_TYPES = (mr.BOTTLE_OPEN, mr.PARTIAL_SALE, mr.VOLUME_REFUND, mr.VOLUME_WASTAGE,
                       mr.VOLUME_ADJUST, mr.VOLUME_DEFICIT_OFFSET)
AUDIT_ACTIONS = (
    "sale.completed", "sale.voided", "sale.discount_applied", "sale.price_changed", "sale.item_removed",
    "sale.item_quantity_reduced", "refund.requested", "refund.approved", "refund.rejected", "refund.cancelled",
    "refund.paid_cash", "refund.paid_mpesa", "stock.adjusted", "stock.volume_wasted", "stock.volume_adjusted",
    "receiving.approved", "shift.closed", "shift.reopened", "pos.session_locked", "pos.session_unlocked",
)


def _sales(day_from, day_to):
    return Sale.objects.filter(status=Sale.COMPLETED, completed_at__date__range=(day_from, day_to))


def _approved_refunds(day_from, day_to):
    return Refund.objects.filter(status__in=(Refund.APPROVED, Refund.PAID),
                                 approved_at__date__range=(day_from, day_to))


def _person_name(first, last, username):
    return f"{first} {last}".strip() or username or "Not recorded"


def by_selling_unit(day_from, day_to):
    """Aggregate sold and refunded lines using the names and volumes frozen on their sale snapshots."""
    data = {}

    def row(product_id, product_name, *, partial, selling_unit, units_per_sale_unit=1, volume=None):
        key = (product_id, product_name, partial, selling_unit, units_per_sale_unit, volume)
        if key not in data:
            label = (f"{selling_unit} ({volume} ml)" if partial else selling_unit)
            data[key] = {
                "product_name": product_name, "selling_unit": label, "is_partial": partial,
                "quantity_sold": 0, "quantity_refunded": 0,
                "full_bottles_sold": 0, "full_bottles_refunded": 0,
                "volume_sold_ml": ZERO, "volume_refunded_ml": ZERO,
                "unknown_full_units_sold": 0, "unknown_full_units_refunded": 0,
                "sales_gross": ZERO, "sales_vat": ZERO, "refunds_gross": ZERO, "refunds_vat": ZERO,
            }
        return data[key]

    sale_items = SaleItem.objects.filter(sale__in=_sales(day_from, day_to))
    for item in (sale_items.filter(total_volume_ml__isnull=True)
                 .values("product_id", "product_name", "pack_name", "units_per_sale_unit")
                 .annotate(sale_quantity=Sum("quantity"),
                           bottles=Sum(F("quantity") * F("units_per_sale_unit")),
                           volume=Sum(F("quantity") * F("full_volume_per_sale_unit_ml")),
                           unknown_volume_units=Sum(Case(
                               When(full_volume_per_sale_unit_ml__isnull=True,
                                    then=F("quantity") * F("units_per_sale_unit")),
                               default=Value(0), output_field=IntegerField())),
                           gross=Sum("line_total"), vat=Sum("tax_amount"))):
        label = item["pack_name"] or "Full bottle/base unit"
        result = row(item["product_id"], item["product_name"], partial=False, selling_unit=label,
                     units_per_sale_unit=item["units_per_sale_unit"])
        result["quantity_sold"] += item["sale_quantity"] or 0
        result["full_bottles_sold"] += item["bottles"] or 0
        result["volume_sold_ml"] += item["volume"] or ZERO
        result["unknown_full_units_sold"] += item["unknown_volume_units"] or 0
        result["sales_gross"] += item["gross"] or ZERO
        result["sales_vat"] += item["vat"] or ZERO

    for item in (sale_items.filter(total_volume_ml__isnull=False)
                 .values("product_id", "product_name", "selling_unit_name", "volume_per_unit_ml")
                 .annotate(sale_quantity=Sum("quantity"), volume=Sum("total_volume_ml"),
                           gross=Sum("line_total"), vat=Sum("tax_amount"))):
        result = row(item["product_id"], item["product_name"], partial=True,
                     selling_unit=item["selling_unit_name"], volume=item["volume_per_unit_ml"])
        result["quantity_sold"] += item["sale_quantity"] or 0
        result["volume_sold_ml"] += item["volume"] or ZERO
        result["sales_gross"] += item["gross"] or ZERO
        result["sales_vat"] += item["vat"] or ZERO

    refund_items = RefundItem.objects.filter(refund__in=_approved_refunds(day_from, day_to))
    for item in (refund_items.filter(sale_item__total_volume_ml__isnull=True)
                 .values("sale_item__product_id", "product_name", "sale_item__pack_name",
                         "sale_item__units_per_sale_unit")
                 .annotate(refund_quantity=Sum("quantity"),
                           bottles=Sum(F("quantity") * F("sale_item__units_per_sale_unit")),
                           volume=Sum(F("quantity") * F("sale_item__full_volume_per_sale_unit_ml")),
                           unknown_volume_units=Sum(Case(
                               When(sale_item__full_volume_per_sale_unit_ml__isnull=True,
                                    then=F("quantity") * F("sale_item__units_per_sale_unit")),
                               default=Value(0), output_field=IntegerField())),
                           gross=Sum("amount"), vat=Sum("tax_amount"))):
        label = item["sale_item__pack_name"] or "Full bottle/base unit"
        result = row(item["sale_item__product_id"], item["product_name"], partial=False, selling_unit=label,
                     units_per_sale_unit=item["sale_item__units_per_sale_unit"])
        result["quantity_refunded"] += item["refund_quantity"] or 0
        result["full_bottles_refunded"] += item["bottles"] or 0
        result["volume_refunded_ml"] += item["volume"] or ZERO
        result["unknown_full_units_refunded"] += item["unknown_volume_units"] or 0
        result["refunds_gross"] += item["gross"] or ZERO
        result["refunds_vat"] += item["vat"] or ZERO

    for item in (refund_items.filter(sale_item__total_volume_ml__isnull=False)
                 .values("sale_item__product_id", "product_name", "selling_unit_name",
                         "sale_item__volume_per_unit_ml")
                 .annotate(refund_quantity=Sum("quantity"), volume=Sum("volume_ml"),
                           gross=Sum("amount"), vat=Sum("tax_amount"))):
        result = row(item["sale_item__product_id"], item["product_name"], partial=True,
                     selling_unit=item["selling_unit_name"], volume=item["sale_item__volume_per_unit_ml"])
        result["quantity_refunded"] += item["refund_quantity"] or 0
        result["volume_refunded_ml"] += item["volume"] or ZERO
        result["refunds_gross"] += item["gross"] or ZERO
        result["refunds_vat"] += item["vat"] or ZERO

    rows = []
    for result in data.values():
        result["net_revenue"] = (result["sales_gross"] - result["sales_vat"]
                                 - result["refunds_gross"] + result["refunds_vat"])
        rows.append(result)
    return sorted(rows, key=lambda item: (item["product_name"].casefold(), item["is_partial"],
                                          item["selling_unit"].casefold()))


def sales_by_day_cashier(day_from, day_to):
    """Daily sales totals by cashier and the supervisor/manager who authorized that shift."""
    day = TruncDate("sale__completed_at", tzinfo=timezone.get_current_timezone())
    rows = (SaleItem.objects.filter(sale__in=_sales(day_from, day_to))
            .values("sale__cashier_id", "sale__cashier__first_name", "sale__cashier__last_name",
                    "sale__cashier__username", "sale__authorized_by__first_name",
                    "sale__authorized_by__last_name", "sale__authorized_by__username",
                    day=day)
            .annotate(
                sale_count=Count("sale_id", distinct=True),
                full_bottles=Sum(Case(
                    When(total_volume_ml__isnull=True, then=F("quantity") * F("units_per_sale_unit")),
                    default=Value(0), output_field=IntegerField())),
                partial_measures=Sum(Case(
                    When(total_volume_ml__isnull=False, then=F("quantity")),
                    default=Value(0), output_field=IntegerField())),
                partial_volume_ml=Sum("total_volume_ml"),
                full_volume_ml=Sum(Case(
                    When(total_volume_ml__isnull=True,
                         then=F("quantity") * F("full_volume_per_sale_unit_ml")),
                    default=Value(ZERO), output_field=VOLUME)),
                unknown_full_units=Sum(Case(
                    When(total_volume_ml__isnull=True, full_volume_per_sale_unit_ml__isnull=True,
                         then=F("quantity") * F("units_per_sale_unit")),
                    default=Value(0), output_field=IntegerField())),
                full_gross=Sum(Case(When(total_volume_ml__isnull=True, then=F("line_total")),
                                    default=Value(ZERO), output_field=MONEY)),
                full_vat=Sum(Case(When(total_volume_ml__isnull=True, then=F("tax_amount")),
                                  default=Value(ZERO), output_field=MONEY)),
                partial_gross=Sum(Case(When(total_volume_ml__isnull=False, then=F("line_total")),
                                       default=Value(ZERO), output_field=MONEY)),
                partial_vat=Sum(Case(When(total_volume_ml__isnull=False, then=F("tax_amount")),
                                     default=Value(ZERO), output_field=MONEY)),
            ).order_by("day", "sale__cashier__username", "sale__authorized_by__username"))
    result = []
    for item in rows:
        item["cashier"] = _person_name(item.pop("sale__cashier__first_name"),
                                       item.pop("sale__cashier__last_name"),
                                       item.pop("sale__cashier__username"))
        item["authorized_by"] = _person_name(item.pop("sale__authorized_by__first_name"),
                                             item.pop("sale__authorized_by__last_name"),
                                             item.pop("sale__authorized_by__username"))
        item["full_revenue"] = (item.pop("full_gross") or ZERO) - (item.pop("full_vat") or ZERO)
        item["partial_revenue"] = (item.pop("partial_gross") or ZERO) - (item.pop("partial_vat") or ZERO)
        item["sales_revenue"] = item["full_revenue"] + item["partial_revenue"]
        item["partial_volume_ml"] = item["partial_volume_ml"] or ZERO
        item["full_volume_ml"] = item["full_volume_ml"] or ZERO
        item["unknown_full_units"] = item["unknown_full_units"] or 0
        item["known_volume_ml"] = item["partial_volume_ml"] + item["full_volume_ml"]
        item["volume_complete"] = item["unknown_full_units"] == 0
        item["total_volume_ml"] = item["known_volume_ml"] if item["volume_complete"] else None
        result.append(item)
    return result


def refunds_by_day(day_from, day_to):
    day = TruncDate("refund__approved_at", tzinfo=timezone.get_current_timezone())
    rows = (RefundItem.objects.filter(refund__in=_approved_refunds(day_from, day_to))
            .values("refund__approved_by__first_name", "refund__approved_by__last_name",
                    "refund__approved_by__username", "refund__requested_by__first_name",
                    "refund__requested_by__last_name", "refund__requested_by__username", day=day)
            .annotate(
                refund_count=Count("refund_id", distinct=True),
                full_bottles=Sum(Case(
                    When(sale_item__total_volume_ml__isnull=True,
                         then=F("quantity") * F("sale_item__units_per_sale_unit")),
                    default=Value(0), output_field=IntegerField())),
                partial_measures=Sum(Case(
                    When(sale_item__total_volume_ml__isnull=False, then=F("quantity")),
                    default=Value(0), output_field=IntegerField())),
                partial_volume_ml=Sum("volume_ml"),
                full_volume_ml=Sum(Case(
                    When(sale_item__total_volume_ml__isnull=True,
                         then=F("quantity") * F("sale_item__full_volume_per_sale_unit_ml")),
                    default=Value(ZERO), output_field=VOLUME)),
                unknown_full_units=Sum(Case(
                    When(sale_item__total_volume_ml__isnull=True,
                         sale_item__full_volume_per_sale_unit_ml__isnull=True,
                         then=F("quantity") * F("sale_item__units_per_sale_unit")),
                    default=Value(0), output_field=IntegerField())),
                gross=Sum("amount"), vat=Sum("tax_amount"),
            ).order_by("day", "refund__approved_by__username"))
    result = []
    for item in rows:
        item["approved_by"] = _person_name(item.pop("refund__approved_by__first_name"),
                                           item.pop("refund__approved_by__last_name"),
                                           item.pop("refund__approved_by__username"))
        item["requested_by"] = _person_name(item.pop("refund__requested_by__first_name"),
                                            item.pop("refund__requested_by__last_name"),
                                            item.pop("refund__requested_by__username"))
        item["partial_volume_ml"] = item["partial_volume_ml"] or ZERO
        item["full_volume_ml"] = item["full_volume_ml"] or ZERO
        item["unknown_full_units"] = item["unknown_full_units"] or 0
        item["known_volume_ml"] = item["partial_volume_ml"] + item["full_volume_ml"]
        item["volume_complete"] = item["unknown_full_units"] == 0
        item["total_volume_ml"] = item["known_volume_ml"] if item["volume_complete"] else None
        item["gross"] = item["gross"] or ZERO
        item["vat"] = item["vat"] or ZERO
        item["net_amount"] = item["gross"] - item["vat"]
        result.append(item)
    return result


def stock_movements(day_from, day_to):
    return StockMovement.objects.filter(created_at__date__range=(day_from, day_to))


def movement_summary(day_from, day_to):
    rows = (stock_movements(day_from, day_to).values("movement_type")
            .annotate(events=Count("pk"), units=Sum("quantity"), volume_ml=Sum("volume_ml"),
                      cost=Sum("total_cost")).order_by("movement_type"))
    return [{"movement_type": row["movement_type"], "label": mr.LABELS[row["movement_type"]],
             "events": row["events"], "units": row["units"] or 0, "volume_ml": row["volume_ml"] or ZERO,
             "cost": row["cost"] or ZERO} for row in rows]


def inventory_position():
    """Current sealed/opened/empty stock and a per-record check against both stock ledgers."""
    latest_capacity = (Bottle.objects.filter(product_id=OuterRef("pk")).order_by("-created_at", "-pk")
                       .values("capacity_ml")[:1])
    latest_stock_balance = (StockMovement.objects.filter(product_id=OuterRef("pk")).order_by("-pk")
                            .values("balance_after")[:1])
    products = (Product.objects.annotate(
        historical_capacity=Subquery(latest_capacity, output_field=VOLUME),
        ledger_units=Subquery(latest_stock_balance, output_field=IntegerField()),
        opened_count=Count("bottles", filter=Q(bottles__status=Bottle.OPENED), distinct=True),
        empty_count=Count("bottles", filter=Q(bottles__status=Bottle.EMPTY), distinct=True),
        opened_volume=Coalesce(
            Sum("bottles__remaining_ml", filter=Q(bottles__status=Bottle.OPENED)),
            Value(ZERO, output_field=VOLUME), output_field=VOLUME),
    ).order_by("name", "pk"))

    volume_deficits = dict(
        StockMovement.objects.filter(movement_type__in=mr.PRODUCT_VOLUME_TYPES)
        .values("product_id").annotate(balance=Sum("volume_ml"))
        .values_list("product_id", "balance"))
    rows, unit_mismatches, unknown_capacity = [], [], 0
    for product in products:
        capacity = product.bottle_capacity_ml or product.historical_capacity
        sealed_volume = product.stock_qty * capacity if capacity is not None else (
            ZERO if product.stock_qty == 0 else None)
        volume_deficit = volume_deficits.get(product.pk, ZERO) or ZERO
        total_volume = None if sealed_volume is None else sealed_volume + product.opened_volume + volume_deficit
        rows.append({
            "product": product, "sealed_bottles": product.stock_qty, "opened_bottles": product.opened_count,
            "empty_bottles": product.empty_count, "opened_volume_ml": product.opened_volume,
            "sealed_volume_ml": sealed_volume, "partial_volume_deficit_ml": volume_deficit,
            "total_volume_ml": total_volume,
            "capacity_known": capacity is not None,
        })
        if sealed_volume is None:
            unknown_capacity += 1
        ledger_units = product.ledger_units if product.ledger_units is not None else 0
        if ledger_units != product.stock_qty:
            unit_mismatches.append({"product": product, "stock_units": product.stock_qty,
                                    "ledger_units": ledger_units, "difference": product.stock_qty - ledger_units})

    movement_balance = (StockMovement.objects.filter(
        bottle_id=OuterRef("pk"), movement_type__in=VOLUME_LEDGER_TYPES)
        .values("bottle_id").annotate(balance=Sum("volume_ml")).values("balance")[:1])
    bottles = (Bottle.objects.filter(status__in=(Bottle.OPENED, Bottle.EMPTY))
               .select_related("product")
               .annotate(ledger_volume=Subquery(movement_balance, output_field=VOLUME))
               .order_by("product__name", "pk"))
    bottle_mismatches, mismatch_count, mismatch_volume = [], 0, ZERO
    for bottle in bottles:
        ledger_volume = bottle.ledger_volume
        difference = bottle.remaining_ml - (ledger_volume if ledger_volume is not None else ZERO)
        if ledger_volume is None or difference != 0:
            mismatch_count += 1
            mismatch_volume += abs(difference)
            if len(bottle_mismatches) < 100:
                bottle_mismatches.append({
                    "bottle": bottle, "ledger_volume_ml": ledger_volume if ledger_volume is not None else ZERO,
                    "remaining_ml": bottle.remaining_ml, "difference_ml": difference,
                })
    return {
        "products": rows, "unknown_capacity_count": unknown_capacity,
        "unit_mismatches": unit_mismatches[:100], "unit_mismatch_count": len(unit_mismatches),
        "bottle_mismatches": bottle_mismatches, "bottle_mismatch_count": mismatch_count,
        "bottle_mismatch_volume_ml": mismatch_volume,
    }


def audit_activity(day_from, day_to):
    """Relevant sensitive actions, exposed only to users with the existing audit.view permission."""
    return (AuditLog.objects.filter(created_at__date__range=(day_from, day_to))
            .filter(Q(approver__isnull=False) | Q(action__in=AUDIT_ACTIONS))
            .select_related("actor", "approver").order_by("-created_at", "-pk"))


def report(day_from, day_to):
    units = by_selling_unit(day_from, day_to)
    summary = {
        "full_bottles_sold": sum(row["full_bottles_sold"] for row in units),
        "full_bottles_refunded": sum(row["full_bottles_refunded"] for row in units),
        "partial_measures_sold": sum(row["quantity_sold"] for row in units if row["is_partial"]),
        "partial_measures_refunded": sum(row["quantity_refunded"] for row in units if row["is_partial"]),
        "partial_volume_sold_ml": sum((row["volume_sold_ml"] for row in units if row["is_partial"]), ZERO),
        "partial_volume_refunded_ml": sum((row["volume_refunded_ml"] for row in units if row["is_partial"]), ZERO),
        "full_volume_sold_ml": sum((row["volume_sold_ml"] for row in units if not row["is_partial"]), ZERO),
        "full_volume_refunded_ml": sum((row["volume_refunded_ml"] for row in units if not row["is_partial"]), ZERO),
        "unknown_full_units_sold": sum(row["unknown_full_units_sold"] for row in units),
        "unknown_full_units_refunded": sum(row["unknown_full_units_refunded"] for row in units),
        "full_revenue": sum((row["net_revenue"] for row in units if not row["is_partial"]), ZERO),
        "partial_revenue": sum((row["net_revenue"] for row in units if row["is_partial"]), ZERO),
        "refunds_gross": sum((row["refunds_gross"] for row in units), ZERO),
        "refunds_net": sum((row["refunds_gross"] - row["refunds_vat"] for row in units), ZERO),
    }
    known_volume_sold = summary["full_volume_sold_ml"] + summary["partial_volume_sold_ml"]
    known_volume_refunded = summary["full_volume_refunded_ml"] + summary["partial_volume_refunded_ml"]
    summary["known_volume_sold_ml"] = known_volume_sold
    summary["known_volume_refunded_ml"] = known_volume_refunded
    summary["sales_volume_complete"] = summary["unknown_full_units_sold"] == 0
    summary["refund_volume_complete"] = summary["unknown_full_units_refunded"] == 0
    summary["volume_complete"] = summary["sales_volume_complete"] and summary["refund_volume_complete"]
    summary["total_liquid_volume_sold_ml"] = known_volume_sold if summary["sales_volume_complete"] else None
    summary["total_liquid_volume_refunded_ml"] = (known_volume_refunded
                                                  if summary["refund_volume_complete"] else None)
    summary["net_liquid_volume_ml"] = (known_volume_sold - known_volume_refunded
                                       if summary["volume_complete"] else None)
    return {
        "summary": summary, "selling_units": units,
        "sales_by_cashier": sales_by_day_cashier(day_from, day_to),
        "refunds_by_day": refunds_by_day(day_from, day_to),
    }
