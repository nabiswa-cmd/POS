from django.urls import path

from . import views

urlpatterns = [
    path("cart/", views.cart, name="sales-cart"),
    path("cart/add/", views.cart_add, name="sales-cart-add"),
    path("cart/quantity/", views.cart_quantity, name="sales-cart-quantity"),
    path("cart/remove/", views.cart_remove, name="sales-cart-remove"),
    path("cart/discount/", views.cart_discount, name="sales-cart-discount"),
    path("cart/price/", views.cart_price, name="sales-cart-price"),
    path("cart/customer/", views.cart_customer, name="sales-cart-customer"),
    path("cart/void/", views.cart_void, name="sales-cart-void"),
    path("cart/hold/", views.cart_hold, name="sales-cart-hold"),
    path("held/", views.held_list, name="sales-held"),
    path("held/resume/", views.held_resume, name="sales-held-resume"),
    path("checkout/cash/", views.checkout_cash, name="sales-checkout-cash"),
    path("receipt/<int:sale_id>/", views.receipt_original, name="sales-receipt"),
    path("receipt/<int:sale_id>/reprint/", views.receipt_reprint, name="sales-receipt-reprint"),
    path("shift/summary/", views.shift_summary, name="sales-shift-summary"),
]
