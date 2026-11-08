from django.urls import path

from . import views

urlpatterns = [
    path("manage/expenses/", views.expenses, name="expenses"),
    path("manage/reports/", views.financial_report, name="financial-report"),
]
