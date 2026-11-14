"""Phase 4B: refunds. Request -> approve -> pay back, with the controls that stop abuse."""
import json
from decimal import Decimal as D

from django.test import Client, override_settings

from accounts import approval_rules
from core.models import AuditLog
from inventory.models import StockMovement

from . import mpesa_services as ms
from . import refund_rules as rr
from . import refunds, rules, services
from .models import MpesaTransaction, Refund, RefundItem, Sale
from .test_phase4a import CFG
from .tests import SalesCase


class RefundRuleTests(SalesCase):
    def test_pieces_of_several_refunds_add_up_to_exactly_the_line(self):
        # 3 units for 1000.00 (333.33 each): the last unit takes the remainder, never more than the line.
        a = rr.line_refund("1000.00", 3, 0, "0", 1)
        b = rr.line_refund("1000.00", 3, 1, a, 1)
        c = rr.line_refund("1000.00", 3, 2, a + b, 1)
        self.assertEqual((a, b, a + b + c), (D("333.33"), D("333.33"), D("1000.00")))

    def test_refusals(self):
        for args in (("3500", 2, 0, "0", 3), ("3500", 2, 2, "7000", 1), ("3500", 2, 0, "0", 0), ("3500", 2, 0, "0", "x")):
            with self.assertRaises(rules.RuleError, msg=str(args)):
                rr.line_refund(*args)
        with self.assertRaises(rules.RuleError):
            rr.check_reason("  ")
        with self.assertRaises(rules.RuleError):
            rr.check_method("CARD")
        with self.assertRaises(rules.RuleError):
            rr.check_mpesa_room("2000", "1000", "0")
        rr.check_mpesa_room("1000", "1000", "0")
        for bad in ("", "abc", "1234567", "has space12"):
            with self.assertRaises(rules.RuleError, msg=bad):
                rr.check_payout_reference(bad)
        self.assertEqual(rr.check_payout_reference(" abcd1234ef "), "ABCD1234EF")


@override_settings(MPESA=CFG)
class RefundFlowTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.scan(qty=2)                                         # 2 x 3500 = 7000
        self.sale = services.checkout_cash(self.cashier, self.shift, "7000")
        self.item = self.sale.items.get()

    def ask(self, qty=1, method="CASH", restock=True, user=None, shift=None, reason="Customer returned it"):
        return refunds.request_refund(user or self.cashier, shift or self.shift, self.sale.pk,
                                      [{"item_id": self.item.pk, "quantity": qty, "restock": restock}], reason, method)

    def approve(self, refund, **kw):
        return refunds.approve_refund(self.cashier, refund.pk, approval_token=self.approval("refund", refunds.ref(refund.pk)), **kw)

    # ---- asking
    def test_request_records_but_changes_nothing_yet(self):
        r = self.ask()
        self.assertEqual((r.status, r.total, r.method, r.requested_by), (Refund.REQUESTED, D("3500.00"), "CASH", self.cashier))
        self.assertEqual((self.stock(), Sale.objects.get(pk=self.sale.pk).total), (8, D("7000.00")))
        self.assertTrue(r.number.startswith("RF-"))

    def test_cashier_cannot_approve_their_own_request(self):
        r = self.ask()
        with self.assertRaises(services.ApprovalNeeded) as ctx:
            refunds.approve_refund(self.cashier, r.pk)
        self.assertEqual((ctx.exception.action, ctx.exception.object_ref), ("refund", f"refund:{r.pk}"))
        r.refresh_from_db()
        self.assertEqual((r.status, self.stock()), (Refund.REQUESTED, 8))

    def test_cannot_refund_more_than_was_sold_counting_earlier_refunds(self):
        self.ask(qty=1)
        with self.assertRaises(services.SaleError):
            self.ask(qty=2)
        self.ask(qty=1)                                          # the last unit is fine
        with self.assertRaises(services.SaleError):
            self.ask(qty=1)

    def test_a_rejected_or_cancelled_request_frees_the_quantity_again(self):
        r1 = self.ask(qty=2)
        refunds.reject_refund(self.supervisor, r1.pk, "Not our product")
        r2 = self.ask(qty=2)
        refunds.cancel_refund(self.cashier, r2.pk)
        self.assertEqual(self.ask(qty=2).status, Refund.REQUESTED)

    def test_reason_and_lines_are_required(self):
        with self.assertRaises(services.SaleError):
            self.ask(reason=" ")
        with self.assertRaises(services.SaleError):
            self.ask(qty=0)
        with self.assertRaises(services.SaleError):
            refunds.request_refund(self.cashier, self.shift, self.sale.pk, [{"item_id": 999999, "quantity": 1}], "x y z", "CASH")

    def test_a_cashier_cannot_refund_someone_elses_sale(self):
        from tills.models import CashierShift
        other = self.cashier2
        shift = CashierShift.objects.create(till=self.till2, cashier=other, authorized_by=self.supervisor, opening_float=D("0"))
        with self.assertRaises(services.NotAllowed):
            refunds.request_refund(other, shift, self.sale.pk, [{"item_id": self.item.pk, "quantity": 1}], "no good", "CASH")
        with self.assertRaises(services.SaleError):                                   # and cannot even look it up
            refunds.find_sale(other, self.sale.number[-4:])

    def test_cash_sale_cannot_be_refunded_by_mpesa(self):
        with self.assertRaises(services.SaleError):
            self.ask(method="MPESA")

    # ---- approving
    def test_supervisor_token_approves_and_stock_goes_back_at_the_original_cost(self):
        before = self.stock()
        r = self.approve(self.ask(qty=1))
        r.refresh_from_db()
        self.assertEqual((r.status, r.approved_by, self.stock()), (Refund.APPROVED, self.supervisor, before + 1))
        mv = StockMovement.objects.filter(movement_type="REFUND_RETURN").get()
        self.assertEqual((mv.quantity, mv.reference, mv.approved_by), (1, r.number, self.supervisor))
        self.assertTrue(r.items.get().restocked)

    def test_approval_token_is_single_use_and_bound_to_that_refund(self):
        r1, r2 = self.ask(qty=1), self.ask(qty=1)
        token = self.approval("refund", refunds.ref(r1.pk))
        with self.assertRaises(services.SaleError):
            refunds.approve_refund(self.cashier, r2.pk, approval_token=token)          # wrong refund
        refunds.approve_refund(self.cashier, r1.pk, approval_token=token)
        with self.assertRaises(services.SaleError):
            refunds.approve_refund(self.cashier, r1.pk, approval_token=token)          # already approved + used

    def test_supervisor_signed_in_approves_directly_but_not_their_own_request(self):
        r = self.ask()
        self.assertEqual(refunds.approve_refund(self.supervisor, r.pk).status, Refund.APPROVED)
        shift = CashierShift_for(self, self.till2, self.manager)
        mine = refunds.request_refund(self.supervisor, shift, self.sale.pk, [{"item_id": self.item.pk, "quantity": 1}], "faulty bottle", "CASH")
        with self.assertRaises(services.ApprovalNeeded):
            refunds.approve_refund(self.supervisor, mine.pk)
        self.assertEqual(Refund.objects.get(pk=mine.pk).status, Refund.REQUESTED)

    def test_owner_may_approve_their_own(self):
        r = self.ask()
        self.assertEqual(refunds.approve_refund(self.manager, r.pk).approved_by, self.manager)

    def test_approver_can_decide_the_goods_are_not_resaleable(self):
        r = self.approve(self.ask(qty=1, restock=True), restock={self.item.pk: False})
        self.assertEqual((self.stock(), r.items.get().restocked), (8, False))
        self.assertFalse(StockMovement.objects.filter(movement_type="REFUND_RETURN").exists())

    def test_marked_not_resaleable_stays_off_the_shelf(self):
        self.approve(self.ask(qty=1, restock=False))
        self.assertEqual(self.stock(), 8)

    def test_a_refund_cannot_be_approved_twice(self):
        r = self.approve(self.ask())
        with self.assertRaises(services.SaleError):
            refunds.approve_refund(self.manager, r.pk)
        self.assertEqual(self.stock(), 9)

    def test_reject_needs_authority_and_a_reason(self):
        r = self.ask()
        with self.assertRaises(services.NotAllowed):
            refunds.reject_refund(self.cashier, r.pk, "no")
        with self.assertRaises(services.SaleError):
            refunds.reject_refund(self.supervisor, r.pk, " ")
        self.assertEqual(refunds.reject_refund(self.supervisor, r.pk, "Bottle was opened").status, Refund.REJECTED)
        self.assertEqual(self.stock(), 8)

    def test_only_the_requester_can_cancel(self):
        r = self.ask()
        with self.assertRaises(services.NotAllowed):
            refunds.cancel_refund(self.supervisor, r.pk)

    # ---- paying back, cash
    def test_cash_payout_lowers_the_expected_drawer_cash_once(self):
        r = self.approve(self.ask(qty=1))
        self.assertEqual(services.shift_summary(self.shift)["cash_in_drawer_expected"], "12000.00")   # float 5000 + sale 7000
        refunds.pay_cash(self.cashier, self.shift, r.pk)
        s = services.shift_summary(self.shift)
        self.assertEqual((s["cash_in_drawer_expected"], s["cash_refunds"], s["cash_sales"]), ("8500.00", "3500.00", "7000.00"))
        with self.assertRaises(services.SaleError):
            refunds.pay_cash(self.cashier, self.shift, r.pk)

    def test_cannot_pay_cash_before_approval_or_more_than_the_drawer_holds(self):
        r = self.ask(qty=1)
        with self.assertRaises(services.SaleError):
            refunds.pay_cash(self.cashier, self.shift, r.pk)
        self.approve(r)
        s = services.shift_summary(self.shift)
        self.assertEqual(s["refunds_waiting"], "3500.00")
        Refund.objects.filter(pk=r.pk).update(total=D("99999.00"))                         # force a drawer shortfall
        with self.assertRaises(services.SaleError):
            refunds.pay_cash(self.cashier, self.shift, r.pk)

    # ---- records are history
    def test_finished_refunds_cannot_be_edited_or_deleted_and_the_sale_never_changes(self):
        r = self.approve(self.ask(qty=1))
        refunds.pay_cash(self.cashier, self.shift, r.pk)
        r = Refund.objects.get(pk=r.pk)
        r.reason = "changed"
        with self.assertRaises(PermissionError):
            r.save()
        with self.assertRaises(PermissionError):
            r.delete()
        with self.assertRaises(PermissionError):
            RefundItem.objects.get(refund=r).delete()
        sale = Sale.objects.get(pk=self.sale.pk)
        self.assertEqual((sale.status, sale.total), (Sale.COMPLETED, D("7000.00")))

    def test_audit_trail_names_everyone(self):
        r = self.approve(self.ask(qty=1))
        refunds.pay_cash(self.cashier, self.shift, r.pk)
        rows = {a.action: a for a in AuditLog.objects.filter(object_ref=r.number)}
        self.assertEqual(set(rows), {"refund.requested", "refund.approved", "refund.paid_cash"})
        self.assertEqual((rows["refund.approved"].actor, rows["refund.approved"].approver), (self.cashier, self.supervisor))

    # ---- slip
    def test_slip_original_then_marked_duplicate_and_only_when_approved(self):
        r = self.ask(qty=1)
        with self.assertRaises(services.SaleError):
            refunds.mark_printed(self.cashier, r.pk)
        self.approve(r)
        refund, dup = refunds.mark_printed(self.cashier, r.pk)
        text = "\n".join(refunds.format_receipt(refunds.build_receipt(refund, duplicate=dup)))
        for needle in ("REFUND SLIP", r.number, self.sale.number, "3,500.00", "Not a tax document", "Approved by: sup"):
            self.assertIn(needle, text)
        self.assertNotIn("DUPLICATE", text)
        with self.assertRaises(services.NotAllowed):
            refunds.mark_printed(self.cashier, r.pk)                    # a cashier cannot reprint
        refund, dup = refunds.mark_printed(self.supervisor, r.pk)
        self.assertTrue(dup)
        self.assertIn("DUPLICATE", "\n".join(refunds.format_receipt(refunds.build_receipt(refund, duplicate=dup))))


def CashierShift_for(case, till, authorized):
    from tills.models import CashierShift
    return CashierShift.objects.create(till=till, cashier=case.supervisor, authorized_by=authorized, opening_float=D("0"))


@override_settings(MPESA=CFG)
class MpesaRefundTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.scan()
        txn = MpesaTransaction.objects.create(trans_id="SALEMPESA01", source="C2B", amount=D("1000.00"))
        self.sale, _ = ms.checkout_split(self.cashier, self.shift, [txn.pk], "2500")      # 1000 M-PESA + 2500 cash
        self.item = self.sale.items.get()

    def ask(self, method, qty=1):
        return refunds.request_refund(self.cashier, self.shift, self.sale.pk, [{"item_id": self.item.pk, "quantity": qty}], "faulty", method)

    def test_mpesa_refund_is_capped_at_what_was_paid_by_mpesa(self):
        with self.assertRaises(services.SaleError):
            self.ask("MPESA")                                  # the line is 3500 but only 1000 came by M-PESA
        self.assertEqual(self.ask("CASH").total, D("3500.00"))

    def test_mpesa_payback_needs_a_real_code_recorded_by_someone_else_and_never_touches_the_drawer(self):
        # a 1000 mpesa-only sale to refund fully by M-PESA
        self.scan()
        txn = MpesaTransaction.objects.create(trans_id="SALEMPESA02", source="C2B", amount=D("3500.00"))
        sale, _ = ms.checkout_split(self.cashier, self.shift, [txn.pk])
        item = sale.items.get()
        r = refunds.request_refund(self.cashier, self.shift, sale.pk, [{"item_id": item.pk, "quantity": 1}], "faulty", "MPESA")
        refunds.approve_refund(self.cashier, r.pk, approval_token=self.approval("refund", refunds.ref(r.pk)))
        with self.assertRaises(services.SaleError):
            refunds.pay_cash(self.cashier, self.shift, r.pk)                                # wrong method
        with self.assertRaises(services.NotAllowed):
            refunds.mark_mpesa_paid(self.cashier, r.pk, "QWERTY12345")                      # cashier has no authority
        with self.assertRaises(services.SaleError):
            refunds.mark_mpesa_paid(self.supervisor, r.pk, "bad")                           # not a real code
        before = services.shift_summary(self.shift)
        paid = refunds.mark_mpesa_paid(self.supervisor, r.pk, "qwerty12345")
        after = services.shift_summary(self.shift)
        self.assertEqual((paid.status, paid.pay_reference), (Refund.PAID, "QWERTY12345"))
        self.assertEqual(after["cash_in_drawer_expected"], before["cash_in_drawer_expected"])
        self.assertEqual(after["mpesa_refunds_paid"], "3500.00")
        with self.assertRaises(services.SaleError):
            refunds.mark_mpesa_paid(self.supervisor, r.pk, "QWERTY12346")                   # already paid

    def test_one_payback_code_cannot_close_two_refunds(self):
        txn = MpesaTransaction.objects.create(trans_id="SALEMPESA03", source="C2B", amount=D("1000.00"))
        self.scan()
        sale2, _ = ms.checkout_split(self.cashier, self.shift, [txn.pk], "2500")
        for sale in (self.sale, sale2):
            r = refunds.request_refund(self.cashier, self.shift, sale.pk, [{"item_id": sale.items.get().pk, "quantity": 1}], "faulty", "CASH")
            Refund.objects.filter(pk=r.pk).update(method="MPESA", total=D("1000.00"))     # direct setup: M-PESA part only
            Refund.objects.filter(pk=r.pk).update(status="APPROVED")
        a, b = Refund.objects.order_by("id")
        refunds.mark_mpesa_paid(self.supervisor, a.pk, "SAMECODE123")
        with self.assertRaises(services.SaleError):
            refunds.mark_mpesa_paid(self.supervisor, b.pk, "SAMECODE123")


@override_settings(MPESA=CFG)
class RefundEndpointTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.scan(qty=2)
        self.sale = services.checkout_cash(self.cashier, self.shift, "7000")
        self.item = self.sale.items.get()
        self.c = Client()
        self.c.force_login(self.cashier)
        session = self.c.session
        session["pos_till"] = 1
        session.save()

    def post(self, url, body=None):
        return self.c.post(url, json.dumps(body or {}), content_type="application/json")

    def test_whole_journey_through_the_screen_endpoints(self):
        found = self.c.get("/api/sales/refund/find/", {"q": self.sale.number[-4:]}).json()
        self.assertEqual((found["sale"]["id"], found["lines"][0]["left"]), (self.sale.pk, 2))
        request = {"sale_id": self.sale.pk, "reason": "Wrong item", "method": "CASH",
                   "lines": [{"item_id": self.item.pk, "quantity": 1}],
                   "request_key": "endpoint-refund-attempt"}
        made = self.post("/api/sales/refund/request/", request).json()
        self.assertTrue(made["ok"], made)
        retry = self.post("/api/sales/refund/request/", request).json()
        self.assertEqual(retry["refund"]["id"], made["refund"]["id"])
        rid = made["refund"]["id"]
        first = self.post(f"/api/sales/refund/{rid}/approve/")
        self.assertEqual((first.status_code, first.json()["approval_required"], first.json()["object_ref"]), (409, "refund", f"refund:{rid}"))
        token = self.approval("refund", f"refund:{rid}")
        ok = self.post(f"/api/sales/refund/{rid}/approve/", {"approval_token": token}).json()
        self.assertEqual((ok["ok"], ok["refund"]["status"], ok["refund"]["can"]["pay_cash"]), (True, "APPROVED", True))
        paid = self.post(f"/api/sales/refund/{rid}/pay-cash/").json()
        self.assertEqual(paid["refund"]["status"], "PAID")
        slip = self.post(f"/api/sales/refund/{rid}/receipt/").json()
        self.assertTrue(slip["ok"] and any("REFUND SLIP" in ln for ln in slip["lines"]))
        listing = self.c.get("/api/sales/refund/list/").json()
        self.assertEqual([r["status"] for r in listing["refunds"]], ["PAID"])

    def test_errors_are_clean_json(self):
        res = self.post("/api/sales/refund/request/", {"sale_id": self.sale.pk, "reason": "x", "method": "CASH", "lines": "nope"})
        self.assertEqual((res.status_code, res.json()["ok"]), (400, False))
        res = self.post("/api/sales/refund/request/", {
            "sale_id": self.sale.pk, "reason": "Wrong item", "method": "CASH",
            "lines": [{"item_id": self.item.pk, "quantity": 1}]})
        self.assertEqual((res.status_code, res.json()["ok"]), (400, False))
        res = self.post("/api/sales/refund/999/approve/")
        self.assertEqual((res.status_code, res.json()["ok"]), (400, False))
        self.assertEqual(self.c.get("/api/sales/refund/find/", {"q": "ZZZ"}).status_code, 400)
