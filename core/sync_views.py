"""Sync endpoints (online server, token protected) and the manager's Server sync screen (shop PC)."""
import os
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import backup, backup_rules as rules
from . import integration_settings, runtime, sync, windows_restore
from .decorators import perm_required
from .http import bad, client_ip
from .models import SyncReceipt, SyncState
from tills.models import CashierShift


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
    if request.method == "GET" and request.GET.get("restore_status") == "1":
        try:
            return JsonResponse({"ok": True, "restore": windows_restore.read_status()})
        except windows_restore.RestoreOperationError as exc:
            return bad(str(exc), 500)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "test":
                sync.ping()
                messages.success(request, "Connected. The online server accepted our token.")
            elif action == "now":
                ok, text = sync.run_cycle(user=request.user, force=request.POST.get("force") == "1")
                (messages.success if ok else messages.error)(request, text or "Done.")
            elif action == "download_server":
                if not sync.enabled():
                    raise sync.SyncError("Set up server sync before downloading a server backup.")
                folder = Path(settings.BACKUP_DIR) / "server-downloads"
                path = sync.download_latest(folder)
                try:
                    summary = backup.verify_backup(path)
                    rules.check_manifest(summary["manifest"], vendor=rules.engine_key(connection.vendor),
                                         code_migrations=backup.code_migrations())
                except backup.BackupError:
                    path.unlink(missing_ok=True)
                    raise
                response = FileResponse(open(path, "rb"), as_attachment=True, filename=path.name,
                                        content_type="application/zip")
                response["Cache-Control"] = "no-store"
                return response
            elif action == "restore_server":
                if not sync.enabled():
                    raise sync.SyncError("Set up server sync before replacing local data.")
                if CashierShift.objects.filter(closed_at__isnull=True).exists():
                    raise windows_restore.RestoreOperationError(
                        "Close every cashier shift before replacing local data.")
                windows_restore.check_ready()
                folder = Path(settings.BACKUP_DIR) / "server-downloads"
                path = sync.download_latest(folder)
                try:
                    summary = backup.verify_backup(path)
                    rules.check_manifest(summary["manifest"], vendor=rules.engine_key(connection.vendor),
                                         code_migrations=backup.code_migrations())
                except backup.BackupError:
                    path.unlink(missing_ok=True)
                    raise
                windows_restore.launch_restore(path.name)
                messages.warning(
                    request,
                    f"Restore started from {path.name}. The POS server will stop, replace local data, and restart. "
                    "This page will reconnect when it is back.")
            else:
                messages.error(request, "Unknown action.")
        except sync.SyncError as exc:
            messages.error(request, str(exc))
        except backup.BackupError as exc:
            messages.error(request, str(exc))
        except windows_restore.RestoreOperationError as exc:
            messages.error(request, str(exc))
        return redirect(request.path)
    runtime.apply()
    state = SyncState.load()
    last = SyncReceipt.objects.first()
    try:
        restore_status = windows_restore.read_status()
    except windows_restore.RestoreOperationError as exc:
        restore_status = {"state": "error", "error": str(exc)}
    open_shifts = CashierShift.objects.filter(closed_at__isnull=True).count()
    return render(request, "core/manage_sync.html", {
        "state": state, "enabled": sync.enabled(), "url": settings.SYNC_SERVER_URL,
        "interval": settings.SYNC_INTERVAL_MINUTES, "has_token": bool(settings.SYNC_TOKEN),
        "receipts": SyncReceipt.objects.all()[:15], "last_receipt": last,
        "inbox_dir": settings.SYNC_INBOX_DIR, "callbacks": _callback_urls(),
        "restore_status": restore_status, "open_shifts": open_shifts,
        "automatic_restore_available": os.name == "nt" and sync.enabled(),
    })
