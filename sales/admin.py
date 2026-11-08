from django.contrib import admin

from .models import EtimsCreditNote, EtimsSubmission, Payment, Refund, RefundItem, Sale, SaleItem


class ReadOnlyMixin:
    """Sales are history. The admin can look but never add, change or delete."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ItemInline(ReadOnlyMixin, admin.TabularInline):
    model = SaleItem
    extra = 0
    fields = ("product_name", "pack_name", "quantity", "unit_price", "discount", "line_total", "tax_class", "tax_amount", "cost_total")


class PaymentInline(ReadOnlyMixin, admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("method", "status", "amount", "tendered", "change_given", "reference", "received_by")


@admin.register(Sale)
class SaleAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("number", "status", "till", "cashier", "authorized_by", "total", "completed_at")
    list_filter = ("status", "till")
    search_fields = ("number", "customer_name", "cashier__username")
    date_hierarchy = "created_at"
    inlines = [ItemInline, PaymentInline]


@admin.register(EtimsSubmission)
class EtimsAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("invoice_no", "sale", "status", "attempts", "last_attempt_at", "last_error")
    list_filter = ("status",)
    search_fields = ("sale__number", "last_error", "signature")


class RefundItemInline(ReadOnlyMixin, admin.TabularInline):
    model = RefundItem
    extra = 0
    fields = ("product_name", "quantity", "amount", "tax_amount", "restock", "restocked")


@admin.register(Refund)
class RefundAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("number", "sale", "status", "method", "total", "requested_by", "approved_by", "paid_by", "requested_at")
    list_filter = ("status", "method")
    search_fields = ("number", "sale__number", "pay_reference", "reason")
    inlines = [RefundItemInline]


@admin.register(EtimsCreditNote)
class EtimsCreditNoteAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("invoice_no", "original_invoice_no", "refund", "status", "attempts", "last_attempt_at", "last_error")
    list_filter = ("status",)
    search_fields = ("refund__number", "refund__sale__number", "last_error", "signature")
