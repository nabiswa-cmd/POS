from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("api/receipt/printer/", views.receipt_printer, name="receipt-printer"),
    path("offline/", views.offline, name="offline"),
    path("manifest.webmanifest", views.manifest, name="pwa-manifest"),
    path("service-worker.js", views.service_worker, name="service-worker"),
    path("app/<str:mode>/", views.app, name="app"),
]
