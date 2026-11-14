from django.contrib import admin
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.urls import include, path, re_path
from django.views.static import serve

from sales import recon_views, refund_views
from sales import report_views as sales_report_views
from sales import views as sales_views
from core import backup_views
from tills import cashup_views
from tills import views as tills_views

admin.site.site_header = "Wines & Spirits POS: Manager Admin"
admin.site.site_title = "POS Admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("pos/sell/", sales_views.pos_page, name="pos-sell"),
    path("manage/tills/", tills_views.overview, name="tills-overview"),
    path("manage/reconciliation/", recon_views.reconciliation, name="reconciliation"),
    path("manage/refunds/", refund_views.refunds, name="refunds-overview"),
    path("manage/sales-report/", sales_report_views.sales_report, name="sales-report"),
    path("manage/cashups/", cashup_views.cashups, name="cashups"),
    path("manage/cashups/<int:shift_id>/", cashup_views.cashup_detail, name="cashup-detail"),
    path("manage/cashups/<int:shift_id>/slip/", cashup_views.cashup_slip, name="cashup-slip"),
    path("pos/cashup/", cashup_views.pos_cashup, name="pos-cashup"),
    path("manage/backups/", backup_views.backups, name="backups"),
    path("manage/sales/<int:sale_id>/reprint/", sales_views.reprint_page, name="sale-reprint"),
    path("api/", include("accounts.urls")),
    path("api/pos/", include("tills.urls")),
    path("api/sales/", include("sales.urls")),
    path("api/pay/", include("sales.mpesa_urls")),    # Safaricom callbacks
    path("", include("catalog.urls")),
    path("", include("inventory.urls")),
    path("", include("accounting.urls")),
    # Product images: only for signed-in staff (not a public folder).
    re_path(r"^media/(?P<path>.*)$", login_required(serve), {"document_root": settings.MEDIA_ROOT}),
    path("", include("core.urls")),
]
