from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Wines & Spirits POS: Manager Admin"
admin.site.site_title = "POS Admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/pos/", include("tills.urls")),
    path("", include("core.urls")),
]
