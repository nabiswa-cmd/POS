"""Public callback endpoints for Safaricom (no login, no CSRF: Safaricom cannot do either). The only
protection is the long random secret in the address (MPESA_CALLBACK_SECRET) plus the strict parsing in
mpesa_rules; do not publish the address. These must be reachable on a public HTTPS address (see README).
Safaricom is always answered "accepted" for well-formed calls so it does not retry forever; money is
recorded exactly once because of the database's unique receipt code."""
import hmac
import json

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import mpesa_rules as rules
from . import mpesa_services as svc

ACK = {"ResultCode": 0, "ResultDesc": "Accepted"}
MAX_BODY = 64 * 1024


def _check(secret):
    expected = settings.MPESA["CALLBACK_SECRET"]
    if not expected or not hmac.compare_digest(str(secret), expected):
        raise Http404()


def _payload(request):
    if len(request.body) > MAX_BODY:
        raise rules.MpesaError("Too large.")
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise rules.MpesaError("Not JSON.")


@csrf_exempt
@require_POST
def c2b_validate(request, secret):
    _check(secret)
    return JsonResponse(ACK)          # we accept every payment to the till; matching happens at the counter


@csrf_exempt
@require_POST
def c2b_confirm(request, secret):
    _check(secret)
    try:
        svc.record_c2b(_payload(request))
    except rules.MpesaError:
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Rejected"}, status=400)
    return JsonResponse(ACK)


@csrf_exempt
@require_POST
def stk_result(request, secret):
    _check(secret)
    try:
        svc.record_stk_callback(_payload(request))
    except rules.MpesaError:
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Rejected"}, status=400)
    return JsonResponse(ACK)
