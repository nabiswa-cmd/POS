from django.urls import path

from . import views

urlpatterns = [
    path("state/", views.state, name="pos-state"),
    path("authorize/", views.authorize, name="pos-authorize"),
    path("authorization/cancel/", views.cancel_authorization, name="pos-authorization-cancel"),
    path("till/change/", views.change_till, name="pos-till-change"),
    path("cashier-login/", views.cashier_login, name="pos-cashier-login"),
    path("cashier/change/", views.change_cashier, name="pos-cashier-change"),
    path("lock/", views.lock_pos, name="pos-lock"),
    path("unlock/", views.unlock_pos, name="pos-unlock"),
    path("shift/open/", views.open_shift, name="pos-shift-open"),
    path("shift/", views.shift_state, name="pos-shift"),
]
