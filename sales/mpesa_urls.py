from django.urls import path

from . import mpesa_views as v

# Safaricom refuses callback addresses that contain words like "mpesa" or "safaricom", hence /api/pay/.
urlpatterns = [
    path("c2b/validate/<str:secret>/", v.c2b_validate, name="pay-c2b-validate"),
    path("c2b/confirm/<str:secret>/", v.c2b_confirm, name="pay-c2b-confirm"),
    path("stk/<str:secret>/", v.stk_result, name="pay-stk"),
]
