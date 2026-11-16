"""Sync endpoints (online server, token protected) and the manager's Server sync screen (shop PC)."""
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import backup_rules as rules
from . import integration_settings, runtime, sync
from .decorators import perm_required
from .http import bad, client_ip
from .models import SyncReceipt, SyncState


def _guard(request):
    """404 unless a sync token is configured here AND the caller sends it: the endpoints do not exist otherwise."""
    runtime.apply()
    if not sync.token_ok(request.headers.get("Authorization")):
        raise Http404()


@csrf_exempt
@require_GET
def api_ping(request):
    _guard(request)
    return JsonResponse({"ok": True, "time": timezone.now().isoformat()})


@csrf_exempt
@require_POST
def api_upload(request):
    _guard(request)
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    try:
        result = sync.receive(request, length, request.GET.get("name", ""),
                              request.headers.get("X-Content-SHA256", ""), client_ip(request))
    except sync.SyncError as exc:
        return bad(str(exc), 400)
    return JsonResponse({"ok": True, **result})


@csrf_exempt
@require_GET
def api_status(request):
    _guard(request)
    last = SyncReceipt.objects.first()
    return JsonResponse({"ok": True, "latest": None if last is None else {
        "name": last.name, "size": last.size, "received_at": last.received_at.isoformat()}})


@csrf_exempt
@require_GET
def api_latest(request):
    """Disaster recovery: a new shop PC downloads the newest backup."""
    _guard(request)
    path = sync.newest_received()
    if path is None:
        raise Http404()
    response = FileResponse(open(path, "rb"), as_attachment=True, filename=path.name, content_type="application/zip")
    response["Cache-Control"] = "no-store"
    response["X-Content-SHA256"] = rules.sha256_file(path)
    return response


@csrf_exempt
@require_POST
def api_tunnel_key(request):
    """A new shop PC registers its tunnel public key (only when the administrator switched this on)."""
    _guard(request)
    import json
    try:
        key = json.loads(request.body.decode("utf-8") or "{}").get("public_key", "")
    except (ValueError, UnicodeDecodeError):
        return bad("Unreadable request.", 400)
    try:
        result = sync.enrol_tunnel_key(key)
    except sync.SyncError as exc:
        return bad(str(exc), 400)
    return JsonResponse({"ok": True, "result": result, "port": settings.SYNC_TUNNEL_PORT})


def _callback_urls():
    try:
        m = integration_settings.mpesa()
    except Exception:
        return None
    base, secret = (m.get("CALLBACK_BASE_URL") or "").rstrip("/"), m.get("CALLBACK_SECRET") or ""
    if not (base and secret):
        return None
    return [("C2B confirmation", f"{base}/api/pay/c2b/confirm/{secret}/"),
            ("C2B validation", f"{base}/api/pay/c2b/validate/{secret}/"),
            ("STK Push result", f"{base}/api/pay/stk/{secret}/")]


@perm_required("settings.manage")
def sync_page(request):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "test":
                sync.ping()
                messages.success(request, "Connected. The online server accepted our token.")
            elif action == "now":
                ok, text = sync.run_cycle(user=request.user, force=request.POST.get("force") == "1")
                (messages.success if ok else messages.error)(request, text or "Done.")
            else:
                messages.error(request, "Unknown action.")
        except sync.SyncError as exc:
            messages.error(request, str(exc))
        return redirect(request.path)
    runtime.apply()
    state = SyncState.load()
    last = SyncReceipt.objects.first()
    return render(request, "core/manage_sync.html", {
        "state": state, "enabled": sync.enabled(), "url": settings.SYNC_SERVER_URL,
        "interval": settings.SYNC_INTERVAL_MINUTES, "has_token": bool(settings.SYNC_TOKEN),
        "receipts": SyncReceipt.objects.all()[:15], "last_receipt": last,
        "inbox_dir": settings.SYNC_INBOX_DIR, "callbacks": _callback_urls(),
    })
