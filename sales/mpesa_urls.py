from django.urls import path

from . import mpesa_views as v

# Safaricom refuses callback addresses that contain words like "mpesa" or "safaricom", hence /api/pay/.
urlpatterns = [
    path("c2b/validate/<str:secret>/", v.c2b_validate, name="pay-c2b-validate"),
    path("c2b/confirm/<str:secret>/", v.c2b_confirm, name="pay-c2b-confirm"),
    path("stk/<str:secret>/", v.stk_result, name="pay-stk"),
    path("txstatus/result/<str:secret>/", v.status_result, name="pay-txstatus-result"),
    path("txstatus/timeout/<str:secret>/", v.status_timeout, name="pay-txstatus-timeout"),
    path("relay/<str:secret>/", v.relay_feed, name="pay-relay"),
]
