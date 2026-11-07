from django.contrib import admin

from .models import AuditLog, ReceiptSettings, ShopSettings


class SingletonAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not self.model.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ShopSettings)
class ShopSettingsAdmin(SingletonAdmin):
    def get_readonly_fields(self, request, obj=None):
        # Costing method may only change while no sale has locked it.
        return ["costing_locked"] + (["costing_method"] if obj and obj.costing_locked else [])


@admin.register(ReceiptSettings)
class ReceiptSettingsAdmin(SingletonAdmin):
    pass


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "approver", "object_type", "object_ref", "till_number")
    list_filter = ("action",)
    search_fields = ("action", "object_ref", "reason", "actor__username", "approver__username")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
