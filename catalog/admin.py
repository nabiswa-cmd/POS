from django.contrib import admin

from .models import Brand, Category, Packaging, Product, ProductCode, Shelf, ShelfTransfer, Supplier


class ReadOnlyAdmin(admin.ModelAdmin):
    """Catalogue changes go through the manager screens, which check permissions and write the audit
    trail (price and cost changes in particular). The Django admin only lets managers look."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(ReadOnlyAdmin):
    list_display = ("name", "sku", "brand", "category", "selling_price", "avg_cost", "stock_qty", "shelf", "active")
    list_filter = ("active", "category", "shelf")
    search_fields = ("name", "sku", "codes__code")


for _model in (Category, Brand, Supplier, Shelf, Packaging, ProductCode, ShelfTransfer):
    admin.site.register(_model, ReadOnlyAdmin)
