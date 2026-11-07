from django.contrib import admin
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.urls import include, path, re_path
from django.views.static import serve

from sales import views as sales_views
from tills import views as tills_views

admin.site.site_header = "Wines & Spirits POS: Manager Admin"
admin.site.site_title = "POS Admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("pos/sell/", sales_views.pos_page, name="pos-sell"),
    path("manage/tills/", tills_views.overview, name="tills-overview"),
    path("manage/sales/<int:sale_id>/reprint/", sales_views.reprint_page, name="sale-reprint"),
    path("api/", include("accounts.urls")),
    path("api/pos/", include("tills.urls")),
    path("api/sales/", include("sales.urls")),
    path("", include("catalog.urls")),
    path("", include("inventory.urls")),
    # Product images: only for signed-in staff (not a public folder).
    re_path(r"^media/(?P<path>.*)$", login_required(serve), {"document_root": settings.MEDIA_ROOT}),
    path("", include("core.urls")),
]
