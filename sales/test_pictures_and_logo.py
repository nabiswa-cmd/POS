"""The till shows a product's picture (or the POS logo when it has none) and every receipt starts with the logo."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from unittest.mock import patch

from catalog import views as catalog_views
from core.models import ReceiptSettings

from . import printing, services, views
from .tests import SalesCase


class ProductPictureTests(SalesCase):
    def test_product_without_a_picture_has_an_empty_image_so_the_till_shows_the_logo(self):
        self.assertEqual(self.product.image_url, "")
        sale, _line = self.scan()
        item = views.cart_payload(sale)["items"][0]
        self.assertEqual(item["image"], "")
        self.assertEqual(catalog_views._product_json(self.product)["image"], "")

    def test_product_with_a_picture_sends_its_address_to_the_till(self):
        self.product.image.name = "products/jw-black.jpg"
        self.product.save()
        sale, _line = self.scan()
        item = views.cart_payload(sale)["items"][0]
        self.assertEqual(item["image"], "/media/products/jw-black.jpg")
        self.assertEqual(catalog_views._product_json(self.product)["image"], "/media/products/jw-black.jpg")

    def test_cart_endpoint_includes_the_image_field(self):
        self.scan()
        self.client.force_login(self.cashier)
        session = self.client.session
        session["pos_till"] = 1
        session.save()
        res = self.client.get("/api/sales/cart/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("image", res.json()["cart"]["items"][0])

    def test_pos_page_has_the_picture_row_and_the_logo_fallback(self):
        self.client.force_login(self.cashier)
        session = self.client.session
        session["pos_till"] = 1
        session.save()
        page = self.client.get("/pos/sell/")
        html = page.content.decode()
        self.assertIn('id="prodrow"', html)
        self.assertIn("/static/core/logo-mark.png", html)      # shown when a product has no picture
        self.assertIn("/static/core/receipt-logo.png", html)   # printed at the top of receipts and slips
        self.assertIn("img.logo", html)
        self.assertIn("height:18mm", html)
        self.assertIn('html,body{width:" + mm + "mm;margin:0 auto', html)


class ReceiptLogoTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.scan()
        self.done = services.checkout_cash(self.cashier, self.shift, "4000")

    def reprint(self, paper):
        cfg = ReceiptSettings.load()
        cfg.paper_width_mm = paper
        cfg.save()
        self.client.force_login(self.supervisor)
        return self.client.post(f"/manage/sales/{self.done.pk}/reprint/")

    def test_duplicate_receipt_page_has_the_logo_above_the_text(self):
        res = self.reprint(80)
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('class="logo"', html)
        self.assertIn("/static/core/receipt-logo.png", html)
        self.assertLess(html.index('class="logo"'), html.index("<pre>"))
        self.assertIn("height:18mm", html)
        self.assertIn("html,body{width:80mm;margin:0 auto", html)

    def test_logo_fits_the_58mm_receipt_width(self):
        html = self.reprint(58).content.decode()
        self.assertIn("height:18mm", html)
        self.assertIn("max-width:calc(58mm - 4mm)", html)
        self.assertIn("html,body{width:58mm;margin:0 auto", html)


class ReceiptPrinterSettingsTests(SalesCase):
    def test_printer_name_is_read_from_database_by_authenticated_app_sessions(self):
        self.assertEqual(self.client.get("/api/receipt/printer/").status_code, 401)

        self.client.force_login(self.cashier)
        settings = ReceiptSettings.load()
        settings.printer_name = "Shop Receipt Printer"
        settings.save()

        response = self.client.get("/api/receipt/printer/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "printer_name": "Shop Receipt Printer"})

    def test_database_printer_setting_overrides_the_environment_fallback(self):
        settings = ReceiptSettings.load()
        settings.printer_name = "Database Printer"
        settings.save()
        with patch.dict("os.environ", {"WINESPOS_RECEIPT_PRINTER": "Legacy Environment Printer"}):
            self.assertEqual(printing.configured_printer(), "Database Printer")

    def test_empty_database_printer_keeps_environment_fallback(self):
        settings = ReceiptSettings.load()
        settings.printer_name = ""
        settings.save()
        with patch.dict("os.environ", {"WINESPOS_RECEIPT_PRINTER": "Legacy Environment Printer"}):
            self.assertEqual(printing.configured_printer(), "Legacy Environment Printer")
