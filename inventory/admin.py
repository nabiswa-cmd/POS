from django.contrib import admin

from .models import Bottle, Receiving, ReceivingItem, StockLot, StockMovement


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockMovement)
class StockMovementAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "product", "movement_type", "quantity", "volume_ml", "balance_after", "user", "approved_by", "reference")
    list_filter = ("movement_type",)
    search_fields = ("product__name", "reference", "reason", "operation_key")
    date_hierarchy = "created_at"


class ItemInline(admin.TabularInline):
    model = ReceivingItem
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Receiving)
class ReceivingAdmin(ReadOnlyAdmin):
    list_display = ("number", "created_at", "supplier", "status", "created_by", "decided_by")
    list_filter = ("status",)
    inlines = [ItemInline]


admin.site.register(StockLot, ReadOnlyAdmin)


@admin.register(Bottle)
class BottleAdmin(ReadOnlyAdmin):
    list_display = ("product", "status", "remaining_ml", "capacity_ml", "opened_at", "opened_by")
    list_filter = ("status",)
    search_fields = ("product__name", "product__sku")
    date_hierarchy = "opened_at"
