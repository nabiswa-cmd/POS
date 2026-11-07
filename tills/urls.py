from django.urls import path

from . import views

urlpatterns = [
    path("state/", views.state, name="pos-state"),
    path("authorize/", views.authorize, name="pos-authorize"),
    path("cashier-login/", views.cashier_login, name="pos-cashier-login"),
    path("shift/open/", views.open_shift, name="pos-shift-open"),
    path("shift/", views.shift_state, name="pos-shift"),
]
