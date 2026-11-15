from django.urls import path

from . import pages

urlpatterns = [
    path("pos/sell/", pages.sell_page, name="pos-sell"),
    path("manage/sales/", pages.manage_sales, name="manage-sales"),
    path("manage/sales/<int:pk>/", pages.manage_sale_detail, name="manage-sale-detail"),
    path("manage/tills/", pages.manage_tills, name="manage-tills"),
]
