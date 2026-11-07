from django.conf import settings
from django.db import connection
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from .models import ShopSettings

AREAS = {
    "pos": "OPEN POS / CASHIER",
    "manager": "MANAGER LOGIN",
    "receiving": "STOCK RECEIVING",
}


def home(request):
    return redirect("app", mode="manager")


@ensure_csrf_cookie
@require_GET
def app(request, mode):
    if mode not in AREAS:
        raise Http404
    shop = ShopSettings.load()
    return render(request, "core/app.html", {
        "mode": mode,
        "area_title": AREAS[mode],
        "shop_name": shop.shop_name,
        "biometric": settings.BIOMETRIC_ENABLED,
    })


@require_GET
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"ok": True})
