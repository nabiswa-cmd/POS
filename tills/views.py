"""POS opening flow: supervisor authorization, then cashier login, then shift check."""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import login
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts import approval_rules, services
from accounts.models import Approval
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
    return JsonResponse({"ok": True, "till": till.number, "authorized_by": approval.approver.display_name,
                         "owner": services.is_exempt(approval.approver)})


@post_json
def cashier_login(request, data):
    """Step 2: the cashier signs in with their own credentials under the authorization."""
    ip = client_ip(request)
    try:
        user, method = services.authenticate(
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
        if authorization.approval.approver_id == user.pk and not services.is_exempt(user):
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
    elif services.is_exempt(user):
        # The shop owner needs nobody to authorize them. We still write the same records (an approval
        # that is used up at once, and a POS authorization) so shifts, sales and receipts stay consistent.
        till = _till_from(data)
        if till is None:
            return bad("Choose a valid till.")
        shift = _open_shift(till)
        if shift is not None and shift.cashier_id != user.pk:
            return bad(f"This till already has an open shift for {shift.cashier.display_name}.", 409)
        now = timezone.now()
        _raw, token_hash = approval_rules.new_token()
        with transaction.atomic():
            approval = Approval.objects.create(
                action="open_pos", requester=None, approver=user, method=method,
                reason="Owner opened the POS session", till_number=till.number,
                token_hash=token_hash, expires_at=now, consumed_at=now)
            authorization = PosAuthorization.objects.create(
                till=till, approval=approval, cashier=user, cashier_logged_in_at=now)
            audit.record("pos.session_opened", actor=user, approver=user, object_type="till",
                         object_ref=till.number, reason="Owner (no separate authorization needed)",
                         ip=ip, till_number=till.number)
        authorized_by = user
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


# --- Manager / supervisor screen: tills, shifts, recent sales, eTIMS status ------------------------------

def _overview_view(request):
    from django.db.models import Count, Sum

    from sales import etims
    from sales.models import EtimsSubmission, Sale

    today = timezone.localdate()
    tills = []
    for till in Till.objects.filter(active=True):
        shift = _open_shift(till)
        done = Sale.objects.filter(till=till, status=Sale.COMPLETED, completed_at__date=today)
        agg = done.aggregate(n=Count("id"), t=Sum("total"))
        tills.append({"till": till, "shift": shift, "count": agg["n"] or 0, "total": agg["t"] or Decimal("0.00")})
    recent = (Sale.objects.filter(status=Sale.COMPLETED).select_related("till", "cashier", "etims")
              .order_by("-completed_at")[:30])
    return render(request, "core/tills_overview.html", {
        "tills": tills, "recent": recent, "can_reprint": request.user.can("receipts.reprint"),
        "etims_on": etims.enabled(), "etims_problems": etims.config_problems() if etims.enabled() else [],
        "etims_counts": etims.counts(), "REJECTED": EtimsSubmission.REJECTED,
    })


from core.decorators import perm_required  # noqa: E402

overview = perm_required("shifts.view_all", "sales.view_all")(_overview_view)
