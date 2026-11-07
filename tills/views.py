"""POS opening flow: supervisor authorization, then cashier login, then shift check."""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import login
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts import services
from accounts.views import BACKEND, user_payload
from core import audit
from core.http import bad, client_ip, login_required_json, post_json
from core.models import ShopSettings

from .models import CashierShift, PosAuthorization, Till

MAX_FLOAT = Decimal("10000000")


def _shift_payload(shift):
    if shift is None:
        return None
    return {"id": shift.pk, "cashier": shift.cashier.display_name,
            "opened_at": shift.opened_at.isoformat(), "opening_float": str(shift.opening_float)}


def _open_shift(till):
    return CashierShift.objects.filter(till=till, closed_at__isnull=True).select_related("cashier").first()


def _till_from(data):
    try:
        return Till.objects.get(number=int(data.get("till")), active=True)
    except (TypeError, ValueError, Till.DoesNotExist):
        return None


@require_GET
def state(request):
    shop = ShopSettings.load()
    tills = list(Till.objects.filter(active=True).order_by("number").values("number", "name"))
    return JsonResponse({"ok": True, "shop": shop.shop_name, "tills": tills,
                         "biometric": settings.BIOMETRIC_ENABLED})


@post_json
def authorize(request, data):
    """Step 1: a supervisor/manager authorizes opening the POS on a till. Nobody is logged in yet."""
    till = _till_from(data)
    if till is None:
        return bad("Choose a valid till.")
    try:
        _raw, approval = services.grant_approval(
            action="open_pos",
            approver_username=data.get("username"),
            secret=data.get("secret"),
            biometric=data.get("biometric"),
            reason=str(data.get("reason") or "").strip() or "Authorize POS session",
            requester=None,
            till_number=till.number,
            ip=client_ip(request),
        )
    except services.AuthError as exc:
        return bad(str(exc), 403)
    authorization = PosAuthorization.objects.create(till=till, approval=approval)
    request.session["pos_auth_id"] = authorization.pk
    request.session["pos_till"] = till.number
    return JsonResponse({"ok": True, "till": till.number, "authorized_by": approval.approver.display_name})


@post_json
def cashier_login(request, data):
    """Step 2: the cashier signs in with their own credentials under the authorization."""
    ip = client_ip(request)
    try:
        user, _method = services.authenticate(
            data.get("username"), data.get("secret"), data.get("biometric"), context="cashier", ip=ip)
    except services.AuthError as exc:
        return bad(str(exc), 401)
    if not user.can("sales.make"):
        audit.record("auth.area_denied", actor=user, object_type="pos", ip=ip)
        return bad("You do not have access to the POS.", 403)

    authorization = None
    auth_id = request.session.get("pos_auth_id")
    if auth_id:
        authorization = (PosAuthorization.objects.select_related("approval__approver", "till")
                         .filter(pk=auth_id, cashier__isnull=True).first())
        if authorization and timezone.now() >= authorization.approval.expires_at:
            authorization = None

    if authorization is not None:
        till = authorization.till
        if authorization.approval.approver_id == user.pk:
            return bad("The authorizing supervisor cannot also be the cashier. Ask another supervisor to authorize.", 403)
        with transaction.atomic():
            authorization.cashier = user
            authorization.cashier_logged_in_at = timezone.now()
            authorization.save(update_fields=["cashier", "cashier_logged_in_at"])
            approval = authorization.approval
            approval.consumed_at = timezone.now()
            approval.save(update_fields=["consumed_at"])
            audit.record("pos.session_opened", actor=user, approver=approval.approver, object_type="till",
                         object_ref=till.number, ip=ip, till_number=till.number)
        authorized_by = approval.approver
    else:
        # Optional manager policy: the same cashier may log back in while their own shift is open.
        till = _till_from(data)
        shift = _open_shift(till) if till else None
        resume_ok = (ShopSettings.load().pos_open_policy == ShopSettings.POLICY_RESUME
                     and shift is not None and shift.cashier_id == user.pk)
        if not resume_ok:
            return bad("Supervisor authorization is required first.", 403)
        authorized_by = shift.authorized_by
        audit.record("pos.session_resumed", actor=user, approver=authorized_by, object_type="till",
                     object_ref=till.number, ip=ip, till_number=till.number)

    login(request, user, backend=BACKEND)
    request.session["pos_till"] = till.number
    request.session["pos_auth_id"] = authorization.pk if authorization else None
    shift = _open_shift(till)
    return JsonResponse({
        "ok": True, "user": user_payload(user), "till": till.number,
        "authorized_by": authorized_by.display_name,
        "shift": _shift_payload(shift if shift and shift.cashier_id == user.pk else None),
        "till_busy_with": shift.cashier.display_name if shift and shift.cashier_id != user.pk else None,
    })


@login_required_json
@post_json
def open_shift(request, data):
    """Step 3: open the cashier's shift on this till with an opening float."""
    till_number = request.session.get("pos_till")
    auth_id = request.session.get("pos_auth_id")
    if not till_number or not request.user.can("sales.make"):
        return bad("Open the POS from the cashier shortcut first.", 403)
    authorization = (PosAuthorization.objects.select_related("approval__approver")
                     .filter(pk=auth_id, cashier=request.user).first()) if auth_id else None
    if authorization is None:
        return bad("Supervisor authorization is required to open a shift.", 403)
    try:
        opening_float = Decimal(str(data.get("opening_float", ""))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return bad("Enter the opening cash float.")
    if not opening_float.is_finite() or opening_float < 0 or opening_float > MAX_FLOAT:
        return bad("Opening float is not valid.")
    try:
        with transaction.atomic():
            till = Till.objects.select_for_update().get(number=till_number)
            if _open_shift(till):
                return bad("This till already has an open shift.", 409)
            shift = CashierShift.objects.create(
                till=till, cashier=request.user, authorized_by=authorization.approval.approver,
                opening_float=opening_float)
            audit.record("shift.opened", actor=request.user, approver=authorization.approval.approver,
                         object_type="shift", object_ref=shift.pk, new={"opening_float": str(opening_float)},
                         ip=client_ip(request), till_number=till.number)
    except IntegrityError:
        return bad("This till already has an open shift.", 409)
    return JsonResponse({"ok": True, "shift": _shift_payload(shift)})


@login_required_json
def shift_state(request):
    till_number = request.session.get("pos_till")
    till = Till.objects.filter(number=till_number).first() if till_number else None
    shift = _open_shift(till) if till else None
    mine = shift if shift and shift.cashier_id == request.user.pk else None
    return JsonResponse({"ok": True, "till": till_number, "shift": _shift_payload(mine)})
