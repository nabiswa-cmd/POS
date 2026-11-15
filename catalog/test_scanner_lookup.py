"""A hardware scanner is a keyboard that types the code and presses Enter. These tests drive the same lookup
the POS calls after every scan, with the kinds of text real scanners send."""
from decimal import Decimal

from django.test import TestCase

from catalog import services
from catalog.models import Product, ProductCode


def make_product(name, sku, barcode):
    p = Product.objects.create(name=name, sku=sku, selling_price=Decimal("500"), buying_cost=Decimal("300"))
    ProductCode.objects.create(product=p, code=sku, kind=ProductCode.KIND_SKU)
    ProductCode.objects.create(product=p, code=barcode, kind=ProductCode.KIND_BARCODE)
    return p


class ScannerLookupTests(TestCase):
    def setUp(self):
        self.tusker = make_product("Tusker 500ml", "TUS500", barcode="6001234567890")
        self.upc = make_product("Imported Whisky", "WHI001", barcode="0036000291452")

    def test_plain_scan(self):
        self.assertEqual(services.find_by_code("6001234567890").product, self.tusker)

    def test_scanner_suffixes_and_wrappers(self):
        for raw in ("6001234567890\r\n", "6001234567890\t", "\x026001234567890\x03", "]E06001234567890", " 6001234567890 "):
            hit = services.find_by_code(raw)
            self.assertIsNotNone(hit, repr(raw))
            self.assertEqual(hit.product, self.tusker, repr(raw))

    def test_upca_scan_finds_the_ean13_the_shop_filed(self):
        self.assertEqual(services.find_by_code("036000291452").product, self.upc)

    def test_ean13_scan_finds_the_upca_the_shop_filed(self):
        other = make_product("Local Gin", "GIN001", barcode="012345678905")
        self.assertEqual(services.find_by_code("0012345678905").product, other)

    def test_exact_code_beats_its_twin(self):
        exact = make_product("Exact twin", "TWIN1", barcode="0555555555555")
        make_product("Other twin", "TWIN2", barcode="555555555555")
        self.assertEqual(services.find_by_code("0555555555555").product, exact)

    def test_unknown_blank_and_inactive(self):
        self.assertIsNone(services.find_by_code("9999999999999"))
        self.assertIsNone(services.find_by_code("\r\n"))
        Product.objects.filter(pk=self.tusker.pk).update(active=False)
        self.assertIsNone(services.find_by_code("6001234567890"))

    def test_sku_and_manual_codes_work_in_any_case(self):
        self.assertEqual(services.find_by_code("tus500").product, self.tusker)
        self.assertEqual(ProductCode.objects.filter(product=self.tusker).count(), 2)
