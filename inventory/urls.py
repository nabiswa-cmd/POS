from django.urls import path

from . import views

urlpatterns = [
    path("manage/stock/", views.stock_list, name="stock-list"),
    path("manage/stock/<int:pk>/", views.stock_history, name="stock-history"),
    path("manage/stock/<int:pk>/adjust/", views.stock_adjust, name="stock-adjust"),
    path("manage/receiving/", views.receiving_list, name="receiving-list"),
    path("manage/receiving/new/", views.receiving_new, name="receiving-new"),
    path("manage/receiving/<int:pk>/", views.receiving_detail, name="receiving-detail"),
    path("manage/receiving/<int:pk>/print/", views.receiving_print, name="receiving-print"),
]
