"""Phase 5A: expenses."""
from datetime import timedelta
from decimal import Decimal as D

from django.test import Client
from django.utils import timezone

from accounts.tests import BaseCase
from core.models import AuditLog
from tills.models import CashierShift, Till

from . import rules, services
from .models import Expense, ExpenseCategory
from .rules import ExpenseError


class ExpenseCase(BaseCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.cat = ExpenseCategory.objects.get(name="Rent")

    def add(self, user=None, **kw):
        args = dict(date=self.today, category_id=self.cat.pk, amount="45000", description="November rent",
                    paid_from="BANK")
        args.update(kw)
        return services.record_expense(user or self.manager, **args)


class RecordTests(ExpenseCase):
    def test_setup_creates_the_default_categories_once(self):
        n = ExpenseCategory.objects.count()
        self.assertEqual(n, len(rules.DEFAULT_CATEGORIES))
        services.ensure_default_categories()
        self.assertEqual(ExpenseCategory.objects.count(), n)

    def test_manager_records_and_it_is_audited(self):
        e = self.add(payee="Landlord", reference="INV-77")
        self.assertEqual((e.amount, e.paid_from, e.payee, e.recorded_by), (D("45000.00"), "BANK", "Landlord", self.manager))
        log = AuditLog.objects.get(action="expense.recorded")
        self.assertEqual((log.actor, log.object_ref), (self.manager, str(e.pk)))

    def test_only_people_with_the_permission_may_record_or_void(self):
        for user in (self.cashier, self.supervisor):
            with self.assertRaises(PermissionError):
                self.add(user=user)
        e = self.add()
        with self.assertRaises(PermissionError):
            services.void_expense(self.supervisor, e.pk, "Entered by mistake")

    def test_bad_input_is_refused(self):
        for kw in ({"amount": "0"}, {"amount": "abc"}, {"description": "x"}, {"paid_from": "PETTY"},
                   {"date": self.today + timedelta(days=1)}, {"category_id": 999999}, {"date": None}):
            with self.assertRaises(ExpenseError, msg=str(kw)):
                self.add(**kw)
        self.assertFalse(Expense.objects.exists())

    def test_inactive_category_cannot_be_used(self):
        self.cat.active = False
        self.cat.save()
        with self.assertRaises(ExpenseError):
            self.add()

    def test_only_drawer_cash_is_linked_to_a_shift(self):
        shift = CashierShift.objects.create(till=Till.objects.get(number=1), cashier=self.cashier,
                                            authorized_by=self.supervisor, opening_float=D("1000"))
        with self.assertRaises(ExpenseError):
            self.add(shift_id=shift.pk)                           # bank payment with a shift
        with self.assertRaises(ExpenseError):
            self.add(paid_from="CASH_TILL")                       # drawer cash without one
        e = self.add(paid_from="CASH_TILL", shift_id=shift.pk, amount="200")
        self.assertEqual((e.shift, e.date), (shift, self.today))

    def test_drawer_cash_needs_an_open_shift_and_cannot_exceed_the_drawer(self):
        shift = CashierShift.objects.create(till=Till.objects.get(number=1), cashier=self.cashier,
                                            authorized_by=self.supervisor, opening_float=D("1000"))
        with self.assertRaises(ExpenseError):
            self.add(paid_from="CASH_TILL", shift_id=shift.pk, amount="1000.01")
        shift.closed_at = timezone.now()
        shift.save(update_fields=["closed_at"])
        with self.assertRaises(ExpenseError):
            self.add(paid_from="CASH_TILL", shift_id=shift.pk, amount="100")


class NeverEditedTests(ExpenseCase):
    def test_edit_and_delete_are_refused(self):
        e = self.add()
        e.amount = D("1")
        with self.assertRaises(PermissionError):
            e.save()
        e = Expense.objects.get(pk=e.pk)
        with self.assertRaises(PermissionError):
            e.save(update_fields=["amount"])
        with self.assertRaises(PermissionError):
            e.delete()

    def test_void_needs_a_reason_works_once_and_freezes_the_row(self):
        e = self.add()
        with self.assertRaises(ExpenseError):
            services.void_expense(self.manager, e.pk, "oops")
        services.void_expense(self.manager, e.pk, "Entered twice by mistake")
        e.refresh_from_db()
        self.assertEqual((e.is_void, e.voided_by, e.void_reason), (True, self.manager, "Entered twice by mistake"))
        with self.assertRaises(ExpenseError):
            services.void_expense(self.manager, e.pk, "Entered twice by mistake")
        e.void_reason = "different"
        with self.assertRaises(PermissionError):
            e.save(update_fields=["void_reason"])
        self.assertEqual(AuditLog.objects.filter(action="expense.voided").count(), 1)


class TotalsTests(ExpenseCase):
    def test_totals_ignore_voided_entries_and_respect_the_period(self):
        power = ExpenseCategory.objects.get(name="Electricity")
        self.add(amount="1000")
        self.add(amount="500", category_id=power.pk, paid_from="MPESA", description="KPLC tokens")
        gone = self.add(amount="9999", description="Wrong entry")
        services.void_expense(self.manager, gone.pk, "Entered on the wrong day")
        self.add(amount="300", date=self.today - timedelta(days=40), description="Old repair")
        self.assertEqual(services.total(self.today, self.today), D("1500.00"))
        self.assertEqual(services.by_category(self.today, self.today), [("Rent", D("1000.00")), ("Electricity", D("500.00"))])
        self.assertEqual(dict(services.by_paid_from(self.today, self.today)),
                         {"Bank": D("1000.00"), "M-PESA (business account)": D("500.00")})
        self.assertEqual(services.total(self.today - timedelta(days=60), self.today), D("1800.00"))

    def test_csv_lists_voided_entries_too_marked_as_such(self):
        e = self.add(description="Wrong entry")
        services.void_expense(self.manager, e.pk, "Entered on the wrong day")
        text = services.csv_for(self.today, self.today)
        self.assertIn("VOIDED", text)
        self.assertIn("Entered on the wrong day", text)


class ScreenTests(ExpenseCase):
    def as_user(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_screen_is_for_expenses_manage_only(self):
        self.assertEqual(Client().get("/manage/expenses/").status_code, 302)
        self.assertEqual(self.as_user(self.cashier).get("/manage/expenses/").status_code, 403)
        self.assertEqual(self.as_user(self.supervisor).get("/manage/expenses/").status_code, 403)
        self.assertEqual(self.as_user(self.manager).get("/manage/expenses/").status_code, 200)

    def test_cashier_cannot_post_an_expense(self):
        self.as_user(self.cashier).post("/manage/expenses/", {"action": "add", "amount": "5"})
        self.assertFalse(Expense.objects.exists())

    def test_add_void_and_download_through_the_screen(self):
        m = self.as_user(self.manager)
        m.post("/manage/expenses/", {"action": "add", "date": self.today.isoformat(), "category": self.cat.pk,
                                     "amount": "1,200", "description": "Shop rent part", "paid_from": "CASH_OTHER"})
        e = Expense.objects.get()
        self.assertEqual(e.amount, D("1200.00"))
        self.assertContains(m.get("/manage/expenses/"), "Shop rent part")
        m.post("/manage/expenses/", {"action": "void", "expense_id": e.pk, "reason": "Paid by the owner instead"})
        e.refresh_from_db()
        self.assertTrue(e.is_void)
        self.assertIn("Shop rent part", m.get("/manage/expenses/?csv=1").content.decode())
