from django.contrib.auth import login, logout
from django.http import JsonResponse

from core import audit
from core.http import bad, client_ip, login_required_json, post_json

from . import services
from .permissions import AREA_PERMS, RESTRICTED_ACTIONS

BACKEND = "django.contrib.auth.backends.ModelBackend"


def user_payload(user):
    return {
        "username": user.username,
        "name": user.display_name,
        "is_manager": user.is_manager,
        "role": user.role.name if user.role_id else ("Manager" if user.is_manager else ""),
        "permissions": sorted(user.permission_codes()),
    }


@post_json
def login_view(request, data):
    """Manager and receiving logins (the POS has its own two-step flow in tills.views)."""
    area = data.get("area")
    if area not in AREA_PERMS:
        return bad("Unknown area.")
    ip = client_ip(request)
    try:
        user, _method = services.authenticate(
            data.get("username"), data.get("secret"), data.get("biometric"), context=area, ip=ip)
    except services.AuthError as exc:
        return bad(str(exc), 401)
    if not user.can(AREA_PERMS[area]):
        audit.record("auth.area_denied", actor=user, object_type=area, ip=ip)
        return bad("You do not have access to this area.", 403)
    login(request, user, backend=BACKEND)
    return JsonResponse({"ok": True, "user": user_payload(user)})


@post_json
def logout_view(request, data):
    logout(request)
    return JsonResponse({"ok": True})


def me_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"ok": True, "user": None})
    return JsonResponse({"ok": True, "user": user_payload(request.user)})


@login_required_json
@post_json
def request_approval_view(request, data):
    """A logged-in user (e.g. a cashier) asks a supervisor/manager to approve a restricted action.

    The approver types their own credentials here; the cashier never sees them. The
    response carries a single-use token that the restricted endpoint must receive.
    """
    action = data.get("action")
    if action not in RESTRICTED_ACTIONS or action == "open_pos":
        return bad("Unknown action.")
    if not services.is_required(action):
        return JsonResponse({"ok": True, "required": False})
    try:
        raw, approval = services.grant_approval(
            action=action,
            approver_username=data.get("approver_username"),
            secret=data.get("secret"),
            biometric=data.get("biometric"),
            reason=str(data.get("reason") or ""),
            requester=request.user,
            object_ref=str(data.get("object_ref") or ""),
            till_number=request.session.get("pos_till"),
            detail=data.get("detail") if isinstance(data.get("detail"), dict) else {},
            ip=client_ip(request),
        )
    except services.AuthError as exc:
        return bad(str(exc), 403)
    return JsonResponse({
        "ok": True, "required": True, "token": raw,
        "approver": approval.approver.display_name,
        "expires_at": approval.expires_at.isoformat(),
    })
