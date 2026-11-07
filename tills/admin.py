from django.contrib import admin

from .models import CashierShift, PosAuthorization, Till


@admin.register(Till)
class TillAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "active")

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashierShift)
class CashierShiftAdmin(ReadOnlyAdmin):
    list_display = ("opened_at", "till", "cashier", "authorized_by", "opening_float", "closed_at")
    list_filter = ("till",)


@admin.register(PosAuthorization)
class PosAuthorizationAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "till", "approval", "cashier", "cashier_logged_in_at")
