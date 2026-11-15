"""Phase 5A: closing a shift and counting the drawer."""
from decimal import Decimal as D

from django.test import Client, override_settings
from django.utils import timezone

from accounting import services as expenses
from accounting.models import ExpenseCategory
from core.models import AuditLog
from sales import mpesa_services as ms
from sales import refunds, services
from sales.models import MpesaTransaction, Sale
from sales.test_phase4a import CFG
from sales.tests import SalesCase

from . import cashup
from . import cashup_rules as rules
from .models import CashierShift, CashUp, Till

# 8 x 1000 + 1 x 500 = 8500: float 5000 + one cash sale of 3500.
COUNT_8500 = {"1000": "8", "500": "1"}


def count_of(amount):
    """The fewest notes that make `amount` (whole shillings), as a cash-up count."""
    out, left = {}, int(amount)
    for d in rules.DENOMINATIONS:
        n, left = divmod(left, d)
        if n:
            out[str(d)] = str(n)
    return out


@override_settings(MPESA=CFG)
class CashupBase(SalesCase):
    def cash_sale(self, qty=1):
        self.scan(qty=qty)
        return services.checkout_cash(self.cashier, self.shift, str(3500 * qty))

    def close(self, counts, reason="", user=None, shift=None):
        return cashup.close_shift(user or self.cashier, (shift or self.shift).pk, counts, reason)


class CloseTests(CashupBase):
    def test_balanced_close_records_everything_and_closes_the_shift(self):
        self.cash_sale()
        cu, warnings = self.close(COUNT_8500)
        self.shift.refresh_from_db()
        self.assertIsNotNone(self.shift.closed_at)
        self.assertEqual((cu.opening_float, cu.cash_sales, cu.expected_cash, cu.counted_cash, cu.variance),
                         (D("5000.00"), D("3500.00"), D("8500.00"), D("8500.00"), D("0.00")))
        self.assertEqual((cu.counts["1000"], cu.counts["500"], cu.counts["200"]), (8, 1, 0))
        self.assertEqual((cu.sales_count, cu.sales_total, warnings), (1, D("3500.00"), []))
        log = AuditLog.objects.filter(action="shift.closed").get()
        self.assertEqual((log.actor, log.till_number), (self.cashier, 1))

    def test_mpesa_money_is_shown_but_never_expected_in_the_drawer(self):
        self.scan()
        txn = MpesaTransaction.objects.create(trans_id="CASHUPMP01", source="C2B", amount=D("3500.00"))
        ms.checkout_split(self.cashier, self.shift, [txn.pk])
        cu, _ = self.close(count_of(5000))                       # only the float is in the drawer
        self.assertEqual((cu.cash_sales, cu.mpesa_sales, cu.expected_cash, cu.variance),
                         (D("0.00"), D("3500.00"), D("5000.00"), D("0.00")))

    def test_split_sale_puts_only_the_cash_part_in_the_drawer(self):
        self.scan()
        txn = MpesaTransaction.objects.create(trans_id="CASHUPMP02", source="C2B", amount=D("1000.00"))
        ms.checkout_split(self.cashier, self.shift, [txn.pk], "2500")
        cu, _ = self.close(count_of(7500))                       # 5000 float + 2500 cash
        self.assertEqual((cu.cash_sales, cu.mpesa_sales, cu.variance), (D("2500.00"), D("1000.00"), D("0.00")))

    def test_cash_refund_leaves_the_drawer_and_mpesa_refund_does_not(self):
        self.scan(qty=2)
        sale = services.checkout_cash(self.cashier, self.shift, "7000")
        item = sale.items.get()
        r = refunds.request_refund(self.cashier, self.shift, sale.pk, [{"item_id": item.pk, "quantity": 1}], "Faulty bottle", "CASH")
        refunds.approve_refund(self.cashier, r.pk, approval_token=self.approval("refund", refunds.ref(r.pk)))
        refunds.pay_cash(self.cashier, self.shift, r.pk)
        snap = cashup.snapshot(self.shift)
        self.assertEqual((snap["cash_refunds"], snap["expected_cash"]), (D("3500.00"), D("8500.00")))   # 5000+7000-3500
        cu, _ = self.close(count_of(8500))
        self.assertEqual((cu.cash_refunds, cu.variance), (D("3500.00"), D("0.00")))

    def test_short_count_needs_a_reason_and_is_stored_negative(self):
        self.cash_sale()
        with self.assertRaises(rules.CashupError) as ctx:
            self.close(count_of(8300))
        self.assertIn("200.00", str(ctx.exception))
        self.shift.refresh_from_db()
        self.assertIsNone(self.shift.closed_at)                  # nothing changed
        self.assertFalse(CashUp.objects.exists())
        cu, _ = self.close(count_of(8300), "Gave change on a 1000 note twice")
        self.assertEqual((cu.variance, cu.reason), (D("-200.00"), "Gave change on a 1000 note twice"))

    def test_over_count_also_needs_a_reason(self):
        self.cash_sale()
        with self.assertRaises(rules.CashupError):
            self.close(count_of(8700))
        cu, _ = self.close(count_of(8700), "Customer left a tip in the drawer")
        self.assertEqual(cu.variance, D("200.00"))

    def test_cannot_close_twice(self):
        self.close(count_of(5000))
        with self.assertRaises(rules.CashupError):
            self.close(count_of(5000))
        self.assertEqual(CashUp.objects.count(), 1)

    def test_a_sale_in_progress_or_on_hold_blocks_closing(self):
        self.scan()
        with self.assertRaises(rules.CashupError):
            self.close(count_of(5000))
        services.hold_sale(self.cashier, self.shift, "waiting")
        with self.assertRaises(rules.CashupError) as ctx:
            self.close(count_of(5000))
        self.assertIn("on hold", str(ctx.exception))
        self.shift.refresh_from_db()
        self.assertIsNone(self.shift.closed_at)

    def test_an_empty_cart_does_not_block_and_is_tidied_away(self):
        sale, line = self.scan()
        services.remove_item(self.cashier, self.shift, line.pk, approval_token=self.approval(
            "remove_item", f"sale:{sale.pk}:item:{line.pk}"))
        self.assertTrue(Sale.objects.filter(shift=self.shift, status=Sale.OPEN).exists())
        self.close(count_of(5000))
        self.assertFalse(Sale.objects.filter(shift=self.shift, status=Sale.OPEN).exists())

    def test_waiting_refunds_are_warned_about_not_counted(self):
        sale = self.cash_sale()
        item = sale.items.get()
        refunds.request_refund(self.cashier, self.shift, sale.pk, [{"item_id": item.pk, "quantity": 1}], "Faulty bottle", "CASH")
        cu, warnings = self.close(COUNT_8500)
        self.assertEqual(cu.variance, D("0.00"))
        self.assertEqual(len(warnings), 1)
        self.assertIn("3500.00", warnings[0])


class WhoMayCloseTests(CashupBase):
    def test_another_cashier_cannot_close_my_shift(self):
        with self.assertRaises(PermissionError):
            self.close(count_of(5000), "trying it", user=self.cashier2)

    def test_a_supervisor_may_close_it_but_must_say_why(self):
        with self.assertRaises(rules.CashupError):
            self.close(count_of(5000), "", user=self.supervisor)
        cu, _ = self.close(count_of(5000), "Cashier went home ill", user=self.supervisor)
        self.assertEqual((cu.closed_by, cu.shift.cashier), (self.supervisor, self.cashier))
        self.assertTrue(AuditLog.objects.get(action="shift.closed").new_value["closed_for_cashier"])

    def test_the_owner_may_close_any_shift(self):
        cu, _ = self.close(count_of(5000), "Month-end clean up", user=self.manager)
        self.assertEqual(cu.closed_by, self.manager)


class DrawerExpenseTests(CashupBase):
    def pay_out(self, amount, shift=None):
        ExpenseCategory.objects.get_or_create(name="Transport")
        return expenses.record_expense(
            self.manager, date=timezone.localdate(), category_id=ExpenseCategory.objects.get(name="Transport").pk,
            amount=amount, description="Boda to the wholesaler", paid_from="CASH_TILL", shift_id=(shift or self.shift).pk)

    def test_cash_paid_out_lowers_expected_cash(self):
        self.cash_sale()
        self.pay_out("300")
        self.assertEqual(cashup.snapshot(self.shift)["expected_cash"], D("8200.00"))
        cu, _ = self.close(count_of(8200))
        self.assertEqual((cu.cash_paid_out, cu.variance), (D("300.00"), D("0.00")))

    def test_f11_summary_and_cashup_use_the_same_numbers(self):
        self.cash_sale()
        self.pay_out("300")
        s = services.shift_summary(self.shift)
        self.assertEqual((s["cash_in_drawer_expected"], s["cash_paid_out"]), ("8200.00", "300.00"))
        cu, _ = self.close(count_of(8200))
        self.assertEqual(str(cu.expected_cash), s["cash_in_drawer_expected"])

    def test_a_voided_payout_comes_back_into_expected_cash(self):
        e = self.pay_out("300")
        expenses.void_expense(self.manager, e.pk, "Entered on the wrong till")
        self.assertEqual(cashup.snapshot(self.shift)["cash_paid_out"], D("0.00"))

    def test_cannot_void_a_payout_once_its_drawer_was_counted(self):
        e = self.pay_out("300")
        self.close(count_of(4700))
        with self.assertRaises(expenses.ExpenseError):
            expenses.void_expense(self.manager, e.pk, "Entered on the wrong till")


class ReopenTests(CashupBase):
    def test_reopen_needs_the_permission_and_a_reason(self):
        self.close(count_of(5000))
        with self.assertRaises(PermissionError):
            cashup.reopen_shift(self.cashier, self.shift.pk, "I miscounted the notes")
        with self.assertRaises(rules.CashupError):
            cashup.reopen_shift(self.manager, self.shift.pk, "oops")

    def test_reopen_keeps_the_old_count_and_allows_a_new_one(self):
        self.cash_sale()
        first, _ = self.close(count_of(8300), "Gave wrong change")
        cashup.reopen_shift(self.manager, self.shift.pk, "Cashier recounted and found the missing notes")
        self.shift.refresh_from_db()
        first.refresh_from_db()
        self.assertIsNone(self.shift.closed_at)
        self.assertEqual((first.superseded_by, first.is_active), (self.manager, False))
        second, _ = self.close(COUNT_8500)
        self.assertEqual((CashUp.objects.filter(shift=self.shift).count(), second.variance), (2, D("0.00")))
        self.assertEqual(CashUp.objects.filter(shift=self.shift, superseded_at__isnull=True).get(), second)
        self.assertEqual(AuditLog.objects.filter(action="shift.reopened").count(), 1)

    def test_cannot_reopen_while_the_till_has_another_open_shift(self):
        self.close(count_of(5000))
        CashierShift.objects.create(till=self.till1, cashier=self.cashier2, authorized_by=self.supervisor,
                                    opening_float=D("1000"))
        with self.assertRaises(rules.CashupError):
            cashup.reopen_shift(self.manager, self.shift.pk, "Counted the wrong drawer")

    def test_cannot_reopen_an_open_shift(self):
        with self.assertRaises(rules.CashupError):
            cashup.reopen_shift(self.manager, self.shift.pk, "Nothing to reopen here")


class RecordIsNeverEditedTests(CashupBase):
    def test_a_cashup_cannot_be_edited_or_deleted(self):
        cu, _ = self.close(count_of(5000))
        cu.counted_cash = D("9999")
        with self.assertRaises(PermissionError):
            cu.save()
        cu.refresh_from_db()
        with self.assertRaises(PermissionError):
            cu.delete()
        with self.assertRaises(PermissionError):
            cu.save(update_fields=["counted_cash"])

    def test_a_superseded_cashup_is_frozen(self):
        cu, _ = self.close(count_of(5000))
        cashup.reopen_shift(self.manager, self.shift.pk, "Counted the wrong drawer")
        cu = CashUp.objects.get(pk=cu.pk)
        cu.supersede_reason = "changed my mind"
        with self.assertRaises(PermissionError):
            cu.save(update_fields=["supersede_reason"])


@override_settings(MPESA=CFG)
class ScreenTests(CashupBase):
    def login(self, user, till=True):
        c = Client()
        c.force_login(user)
        if till:
            s = c.session
            s["pos_till"] = 1
            s.save()
        return c

    def test_cashier_page_never_shows_the_expected_figure_and_closing_signs_out(self):
        self.cash_sale()
        c = self.login(self.cashier)
        page = c.get("/pos/cashup/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "8500")                      # expected cash is not revealed before counting
        self.assertNotContains(page, "Expected")
        post = c.post("/pos/cashup/", {"c_1000": "8", "c_500": "1"})
        self.assertEqual(post.status_code, 200)
        self.assertContains(post, "Balanced")
        self.shift.refresh_from_db()
        self.assertIsNotNone(self.shift.closed_at)
        self.assertEqual(c.get("/pos/cashup/").status_code, 302)  # signed out, sent back to the till login

    def test_a_wrong_count_stays_on_the_page_with_the_message_and_keeps_what_was_typed(self):
        self.cash_sale()
        c = self.login(self.cashier)
        r = c.post("/pos/cashup/", {"c_1000": "8", "c_100": "3"})
        self.assertContains(r, "off by KES")
        self.assertContains(r, 'value="8"')
        self.shift.refresh_from_db()
        self.assertIsNone(self.shift.closed_at)

    def test_no_shift_or_no_login_goes_back_to_the_till_login(self):
        self.assertEqual(Client().get("/pos/cashup/").status_code, 302)
        c = self.login(self.cashier2)
        self.assertEqual(c.get("/pos/cashup/").status_code, 302)  # has no open shift on till 1

    def test_manager_screens_are_permission_gated(self):
        self.close(count_of(5000))
        for url in ("/manage/cashups/", f"/manage/cashups/{self.shift.pk}/", f"/manage/cashups/{self.shift.pk}/slip/"):
            self.assertEqual(self.login(self.cashier).get(url).status_code, 403, url)
            self.assertEqual(self.login(self.supervisor).get(url).status_code, 200, url)
            self.assertEqual(self.login(self.manager).get(url).status_code, 200, url)
            self.assertEqual(Client().get(url).status_code, 302, url)

    def test_list_csv_and_detail_show_the_variance(self):
        self.cash_sale()
        self.close(count_of(8300), "Gave wrong change")
        m = self.login(self.manager)
        self.assertContains(m.get("/manage/cashups/"), "Short 200.00")
        csv_text = m.get("/manage/cashups/?csv=1").content.decode()
        self.assertIn("Expected cash", csv_text)
        self.assertIn("-200.00", csv_text)
        self.assertContains(m.get(f"/manage/cashups/{self.shift.pk}/"), "Gave wrong change")

    def test_owner_can_reopen_from_the_screen_but_cashier_and_supervisor_cannot(self):
        self.close(count_of(5000))
        url = f"/manage/cashups/{self.shift.pk}/"
        self.assertEqual(self.login(self.cashier).post(url, {"action": "reopen", "reason": "I miscounted notes"}).status_code, 403)
        self.login(self.supervisor).post(url, {"action": "reopen", "reason": "Supervisors lack this by default"})
        self.shift.refresh_from_db()
        self.assertIsNotNone(self.shift.closed_at)
        self.login(self.manager).post(url, {"action": "reopen", "reason": "Cashier miscounted the notes"})
        self.shift.refresh_from_db()
        self.assertIsNone(self.shift.closed_at)

    def test_manager_can_close_a_forgotten_shift_from_the_screen(self):
        m = self.login(self.manager)
        m.post(f"/manage/cashups/{self.shift.pk}/", {"action": "close", "c_1000": "5", "reason": "Cashier left without closing"})
        self.shift.refresh_from_db()
        self.assertIsNotNone(self.shift.closed_at)
