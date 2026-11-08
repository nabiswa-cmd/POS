"""Phase 4B-2: an approved refund of an eTIMS-signed sale becomes a KRA credit note (receipt type R)."""
import json
from decimal import Decimal as D
from unittest import mock

from django.test import Client, override_settings

from catalog.models import Product
from core.models import AuditLog, ShopSettings

from . import etims, refunds, services
from .models import EtimsCreditNote, EtimsSubmission, Refund
from .test_phase3b import GOOD, FakeUnit
from .tests import SalesCase

REJECT = {"resultCd": "901", "resultMsg": "Invalid original invoice", "data": {}}
SIGN2 = {"resultCd": "000", "resultMsg": "ok", "data": {"rcptNo": 8, "totRcptNo": 8, "intrlData": "INTERNAL2", "rcptSign": "SIGNCREDIT99",
                                                         "sdcId": "KRACU0100000001", "vsdcRcptPbctDate": "20261108120000"}}


@override_settings(ETIMS_MODE="sandbox", ETIMS_BASE_URL="http://127.0.0.1:8088", ETIMS_BRANCH_ID="00",
                   ETIMS_QR_URL_TEMPLATE="https://example.test/q?p={tin}&b={bhf}&s={sign}")
class CreditNoteTests(SalesCase):
    def setUp(self):
        super().setUp()
        shop = ShopSettings.load()
        shop.vat_registered, shop.kra_pin = True, "P051234567X"
        shop.save()
        Product.objects.filter(pk=self.product.pk).update(etims_item_class_code="50202300")
        self.unit = FakeUnit(GOOD)
        patcher = mock.patch.object(etims, "unit_factory", lambda: self.unit)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sell(self, qty=2, sign=True):
        self.scan(qty=qty)
        sale = services.checkout_cash(self.cashier, self.shift, str(3500 * qty))
        if sign:
            etims.submit(sale.pk)
        return sale

    def refund(self, sale, qty=1, method="CASH"):
        item = sale.items.get()
        r = refunds.request_refund(self.cashier, self.shift, sale.pk, [{"item_id": item.pk, "quantity": qty}], "faulty", method)
        return refunds.approve_refund(self.supervisor, r.pk)

    # ---- creation and numbering
    def test_approval_creates_a_credit_note_pointing_at_the_signed_invoice_with_a_gapless_number(self):
        sale = self.sell()                                       # invoice no 1
        r = self.refund(sale)
        note = r.etims
        self.assertEqual((note.invoice_no, note.original_invoice_no, note.status, note.receipt_type), (2, 1, "PENDING", "R"))
        self.assertEqual(self.sell().etims.invoice_no, 3)        # one shared KRA series

    def test_no_note_while_only_requested_or_when_rejected(self):
        sale = self.sell()
        item = sale.items.get()
        r = refunds.request_refund(self.cashier, self.shift, sale.pk, [{"item_id": item.pk, "quantity": 1}], "faulty", "CASH")
        self.assertFalse(EtimsCreditNote.objects.exists())
        refunds.reject_refund(self.supervisor, r.pk, "Not our product")
        self.assertFalse(EtimsCreditNote.objects.exists())

    def test_sale_that_was_never_on_etims_gets_no_credit_note_and_says_so(self):
        with override_settings(ETIMS_MODE="off"):
            sale = self.sell(sign=False)
        r = self.refund(sale)
        self.assertFalse(EtimsCreditNote.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action="etims.credit_note_skipped", object_ref=r.number).exists())
        text = "\n".join(refunds.format_receipt(refunds.build_receipt(r)))
        self.assertIn("Not a tax document", text)

    # ---- what is sent
    def test_payload_is_a_type_R_message_that_adds_up_to_the_refund(self):
        sale = self.sell()
        r = self.refund(sale, qty=1)
        self.unit.answer = SIGN2
        note = etims.submit_credit_note(r.pk)
        self.assertEqual(note.status, "SUBMITTED")
        endpoint, p = self.unit.sent[-1]
        self.assertEqual(endpoint, "saveTrnsSalesOsdc")
        self.assertEqual((p["rcptTyCd"], p["invcNo"], p["orgInvcNo"], p["salesTyCd"], p["pmtTyCd"], p["tin"]),
                         ("R", 2, 1, "N", "01", "P051234567X"))
        self.assertEqual((p["totAmt"], p["totItemCnt"], p["itemList"][0]["qty"], p["itemList"][0]["totAmt"]), (3500.0, 1, 1, 3500.0))
        line = p["itemList"][0]
        self.assertEqual(round(line["splyAmt"] - line["dcAmt"], 2), line["totAmt"])
        self.assertEqual(round(p["totTaxAmt"], 2), float(r.tax_total))
        self.assertAlmostEqual(p["totTaxblAmt"] + p["totTaxAmt"], p["totAmt"], places=2)

    def test_mpesa_refund_uses_the_mpesa_payment_code(self):
        from . import mpesa_services as ms
        from .models import MpesaTransaction
        self.scan()
        txn = MpesaTransaction.objects.create(trans_id="CRNOTEMPES1", source="C2B", amount=D("3500.00"))
        sale, _ = ms.checkout_split(self.cashier, self.shift, [txn.pk])
        etims.submit(sale.pk)
        r = self.refund(sale, method="MPESA")
        etims.submit_credit_note(r.pk)
        self.assertEqual(self.unit.sent[-1][1]["pmtTyCd"], "06")

    # ---- result handling
    def test_signed_credit_note_stores_only_what_kra_returned_and_the_slip_becomes_a_credit_note(self):
        sale = self.sell()
        r = self.refund(sale)
        self.unit.answer = SIGN2
        note = etims.submit_credit_note(r.pk)
        self.assertEqual((note.signature, note.scu_receipt_no, note.qr_text),
                         ("SIGNCREDIT99", "8", "https://example.test/q?p=P051234567X&b=00&s=SIGNCREDIT99"))
        refunds.pay_cash(self.cashier, self.shift, r.pk)
        refund, dup = refunds.mark_printed(self.cashier, r.pk)
        text = "\n".join(refunds.format_receipt(refunds.build_receipt(refund)))
        for needle in ("CREDIT NOTE", "SIGNCREDIT99", "KRACU0100000001", "INTERNAL2", "Original invoice:", "Credit note ref:"):
            self.assertIn(needle, text)
        self.assertNotIn("Not a tax document", text)

    def test_unsigned_slip_never_claims_to_be_a_credit_note(self):
        r = self.refund(self.sell())
        text = "\n".join(refunds.format_receipt(refunds.build_receipt(Refund.objects.get(pk=r.pk))))
        self.assertIn("REFUND SLIP", text)
        self.assertIn("awaiting KRA signature", text)
        self.assertNotIn("CREDIT NOTE", text)

    def test_offline_keeps_the_refund_moving_and_it_is_signed_later(self):
        sale = self.sell()
        r = self.refund(sale)
        self.unit.error = "Cannot reach the eTIMS unit (offline or not running)."
        note = etims.submit_credit_note(r.pk)
        self.assertEqual((note.status, note.attempts), ("PENDING", 1))
        self.assertIn("offline", note.last_error)
        refunds.pay_cash(self.cashier, self.shift, r.pk)                        # the customer is still paid
        self.assertEqual(Refund.objects.get(pk=r.pk).status, Refund.PAID)
        self.unit.error, self.unit.answer = None, SIGN2
        self.assertEqual(etims.submit_pending(5), 1)
        self.assertEqual(EtimsCreditNote.objects.get().status, "SUBMITTED")

    def test_original_invoice_not_yet_signed_is_sent_first_then_the_credit_note(self):
        sale = self.sell(sign=False)                                            # sale invoice still PENDING
        r = self.refund(sale)
        self.unit.answer = GOOD
        note = etims.submit_credit_note(r.pk)
        self.assertEqual(note.status, "SUBMITTED")
        self.assertEqual([m[1]["rcptTyCd"] for m in self.unit.sent], ["S", "R"])
        self.assertEqual(EtimsSubmission.objects.get(sale=sale).status, "SUBMITTED")

    def test_credit_note_waits_when_the_original_cannot_be_signed(self):
        sale = self.sell(sign=False)
        r = self.refund(sale)
        self.unit.error = "Cannot reach the eTIMS unit (offline or not running)."
        note = etims.submit_credit_note(r.pk)
        self.assertEqual(note.status, "PENDING")
        self.assertIn("original sale invoice", note.last_error)
        self.assertEqual([m for m in self.unit.sent if m[1]["rcptTyCd"] == "R"], [])      # never sent without a signed original

    def test_kra_rejection_is_recorded_and_not_retried_automatically(self):
        r = self.refund(self.sell())
        self.unit.answer = REJECT
        note = etims.submit_credit_note(r.pk)
        self.assertEqual(note.status, "REJECTED")
        self.assertIn("901", note.last_error)
        self.assertTrue(AuditLog.objects.filter(action="etims.credit_note_rejected").exists())
        sent = len(self.unit.sent)
        etims.submit_pending(5)
        self.assertEqual(len(self.unit.sent), sent)

    def test_missing_item_class_code_explains_itself(self):
        sale = self.sell()
        r = self.refund(sale)
        Product.objects.filter(pk=self.product.pk).update(etims_item_class_code="")
        note = etims.submit_credit_note(r.pk)
        self.assertEqual(note.status, "PENDING")
        self.assertIn("class code", note.last_error)

    # ---- history
    def test_signed_credit_note_is_frozen_and_never_deleted(self):
        r = self.refund(self.sell())
        etims.submit_credit_note(r.pk)
        note = EtimsCreditNote.objects.get()
        note.last_error = "tampered"
        with self.assertRaises(PermissionError):
            note.save()
        with self.assertRaises(PermissionError):
            note.delete()

    def test_signing_twice_sends_once(self):
        r = self.refund(self.sell())
        etims.submit_credit_note(r.pk)
        n = len(self.unit.sent)
        etims.submit_credit_note(r.pk)
        self.assertEqual(len(self.unit.sent), n)

    def test_counts_include_credit_notes(self):
        r = self.refund(self.sell())
        etims.submit_credit_note(r.pk)
        self.assertEqual(etims.counts().get("SUBMITTED"), 2)                    # the sale and its credit note

    def test_two_partial_refunds_are_two_credit_notes_with_their_own_numbers(self):
        sale = self.sell()
        a, b = self.refund(sale, qty=1), self.refund(sale, qty=1)
        self.assertEqual((a.etims.invoice_no, b.etims.invoice_no, a.etims.original_invoice_no, b.etims.original_invoice_no), (2, 3, 1, 1))

    # ---- through the screen endpoint
    def test_approve_endpoint_tells_the_screen_the_credit_note_state(self):
        sale = self.sell()
        item = sale.items.get()
        r = refunds.request_refund(self.cashier, self.shift, sale.pk, [{"item_id": item.pk, "quantity": 1}], "faulty", "CASH")
        c = Client()
        c.force_login(self.cashier)
        sess = c.session
        sess["pos_till"] = 1
        sess.save()
        self.unit.answer = SIGN2
        res = c.post(f"/api/sales/refund/{r.pk}/approve/",
                     json.dumps({"approval_token": self.approval("refund", f"refund:{r.pk}")}), content_type="application/json").json()
        self.assertEqual((res["ok"], res["etims"], res["refund"]["etims"]), (True, "SUBMITTED", "SUBMITTED"))
        slip = c.post(f"/api/sales/refund/{r.pk}/receipt/", "{}", content_type="application/json").json()
        self.assertTrue(any("CREDIT NOTE" in ln for ln in slip["lines"]))
        self.assertIn("<svg", slip["qr_svg"])
