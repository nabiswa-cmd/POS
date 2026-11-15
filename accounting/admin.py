from django.contrib import admin

from .models import Expense, ExpenseCategory


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "active")
    list_filter = ("active",)
    search_fields = ("name",)

    def has_delete_permission(self, request, obj=None):
        return False          # a category used by an expense can never go; switch it off instead


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    """Look only. Expenses are recorded and voided on the Expenses screen, where the rules are enforced."""
    list_display = ("date", "category", "amount", "paid_from", "recorded_by", "voided_at")
    list_filter = ("paid_from", "category")
    date_hierarchy = "date"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
