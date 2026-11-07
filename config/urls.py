from django.contrib import admin
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.urls import include, path, re_path
from django.views.static import serve

admin.site.site_header = "Wines & Spirits POS: Manager Admin"
admin.site.site_title = "POS Admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/pos/", include("tills.urls")),
    path("", include("catalog.urls")),
    path("", include("inventory.urls")),
    # Product images: only for signed-in staff (not a public folder).
    re_path(r"^media/(?P<path>.*)$", login_required(serve), {"document_root": settings.MEDIA_ROOT}),
    path("", include("core.urls")),
]
