"""Public callback endpoints for Safaricom (no login, no CSRF: Safaricom cannot do either). The only
protection is the long random secret in the address (MPESA_CALLBACK_SECRET) plus the strict parsing in
mpesa_rules; do not publish the address. These must be reachable on a public HTTPS address (see README).
Safaricom is always answered "accepted" for well-formed calls so it does not retry forever; money is
recorded exactly once because of the database's unique receipt code."""
import hmac
import json

from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core import integration_settings
from core import audit

from . import mpesa_rules as rules
from . import mpesa_services as svc
from . import mpesa_relay as mpesa_relay_log
from . import relay, relay_rules

ACK = {"ResultCode": 0, "ResultDesc": "Accepted"}
MAX_BODY = 64 * 1024


def _check(secret):
    expected = integration_settings.mpesa()["CALLBACK_SECRET"]
    if not expected or not hmac.compare_digest(str(secret), expected):
        raise Http404()


def _payload(request):
    if len(request.body) > MAX_BODY:
        raise rules.MpesaError("Too large.")
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise rules.MpesaError("Not JSON.")


def _source_ip(request):
    cfg = integration_settings.mpesa()
    if cfg.get("TRUST_PROXY"):
        ip = rules.forwarded_ip(request.headers.get("X-Forwarded-For"))
        if ip:
            return ip
    return request.META.get("REMOTE_ADDR") or ""


def _check_ip(request):
    cfg = integration_settings.mpesa()
    nets = rules.parse_ip_list(cfg.get("ALLOWED_IPS", ""))
    ip = _source_ip(request)
    if not rules.ip_allowed(ip, nets):
        audit.record("mpesa.callback_blocked", object_type="mpesa", reason="Source IP is not allowed",
                     new={"ip": ip})
        raise Http404()


@csrf_exempt
def root_c2b_callback(request):
    """POST is the public C2B callback URL. GET and HEAD preserve the normal homepage."""
    if request.method in ("GET", "HEAD"):
        return redirect("app", mode="manager")
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405, headers={"Allow": "GET, HEAD, POST"})
    _check_ip(request)
    try:
        payload = _payload(request)
    except rules.MpesaError:
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Rejected"}, status=400)

    # C2B validation and confirmation are not distinguishable reliably in every Daraja payload.
    # A validation-style message has no usable confirmation receipt, so acknowledge it without recording.
    if not payload.get("TransID") or not payload.get("TransAmount"):
        return JsonResponse(ACK)
    try:
        cfg = integration_settings.mpesa()
        parsed = rules.parse_c2b(payload)
        allowed = {str(cfg.get("TILL_NUMBER") or "").strip(), str(cfg.get("SHORTCODE") or "").strip()}
        allowed.discard("")
        if allowed and parsed["shortcode"] and parsed["shortcode"] not in allowed:
            raise rules.MpesaError("Message came for another shortcode.")
        svc.record_c2b(payload)
    except rules.MpesaError:
        mpesa_relay_log.log_callback("c2b", payload, _source_ip(request), "error")
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Rejected"}, status=400)
    mpesa_relay_log.log_callback("c2b", payload, _source_ip(request), "ok")
    return JsonResponse(ACK)


@csrf_exempt
@require_POST
def c2b_validate(request, secret):
    _check(secret)
    _check_ip(request)
    return JsonResponse(ACK)          # we accept every payment to the till; matching happens at the counter


@csrf_exempt
@require_POST
def c2b_confirm(request, secret):
    _check(secret)
    _check_ip(request)
    payload = None
    try:
        payload = _payload(request)
        svc.record_c2b(payload)
    except rules.MpesaError:
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Rejected"}, status=400)
    mpesa_relay_log.log_callback("c2b", payload, _source_ip(request), "ok")
    return JsonResponse(ACK)


@csrf_exempt
@require_POST
def stk_result(request, secret):
    _check(secret)
    _check_ip(request)
    payload = None
    try:
        payload = _payload(request)
        svc.record_stk_callback(payload)
    except rules.MpesaError:
        if payload is not None:
            mpesa_relay_log.log_callback("stk", payload, _source_ip(request), "error")
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Rejected"}, status=400)
    mpesa_relay_log.log_callback("stk", payload, _source_ip(request), "ok")
    return JsonResponse(ACK)


@csrf_exempt
@require_POST
def status_result(request, secret):
    """Safaricom's answer to a customer-name lookup."""
    _check(secret)
    _check_ip(request)
    try:
        svc.record_status_result(_payload(request))
    except rules.MpesaError:
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Rejected"}, status=400)
    return JsonResponse(ACK)


@csrf_exempt
@require_POST
def status_timeout(request, secret):
    _check(secret)
    _check_ip(request)
    return JsonResponse(ACK)          # the lookup did not happen; the name stays blank


@require_GET
def relay_feed(request, secret):
    """The shop PC asks for recent payments. Needs the secret in the address AND the relay token; 404 otherwise
    (also when no token is configured, so the feed does not exist unless you switch it on)."""
    _check(secret)
    if not relay_rules.bearer_ok(request.headers.get("Authorization"),
                                 integration_settings.mpesa().get("RELAY_TOKEN")):
        raise Http404()
    after, limit, hours = relay_rules.feed_params(request.GET)
    response = JsonResponse(relay.feed(after, limit, hours))
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def relay_export(request):
    """Public relay feed for an online server; protected by a configured bearer token."""
    if not relay_rules.bearer_ok(request.headers.get("Authorization"),
                                 integration_settings.mpesa().get("RELAY_TOKEN")):
        raise Http404()
    after = relay_rules.feed_params(request.GET)[0]
    rows = mpesa_relay_log.export_after(after)
    last_id = rows[-1]["id"] if rows else after
    return JsonResponse({"ok": True, "records": rows, "last_id": last_id}, status=200)
