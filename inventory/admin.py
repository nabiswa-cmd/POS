from django.contrib import admin

from .models import Receiving, ReceivingItem, StockLot, StockMovement


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockMovement)
class StockMovementAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "product", "movement_type", "quantity", "balance_after", "user", "approved_by", "reference")
    list_filter = ("movement_type",)
    search_fields = ("product__name", "reference", "reason")
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
