import json
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import URLError

from django.core.files.base import ContentFile
from django.core.cache import cache
from django.test import TestCase, override_settings
from PIL import Image

from accounts.tests import BaseCase
from catalog import external_lookup
from catalog.models import Product, ProductCode
from catalog.tests import make_product
from tills.models import CashierShift, Till


BARCODE = "3017620422003"


class ImageResponse:
    def __init__(self, url, body):
        self.url = url
        self.body = body
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.body[:limit]


def png_content(name):
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return ContentFile(buffer.getvalue(), name=name)


class ExternalLookupClientTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(OPEN_FOOD_FACTS_ENABLED=True)
    @patch("catalog.external_lookup.urllib.request.urlopen")
    def test_lookup_uses_the_bounded_v3_product_endpoint(self, urlopen):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps({
                    "status": "success",
                    "code": BARCODE,
                    "product": {
                        "code": BARCODE,
                        "product_name": "Example Wine",
                        "brands": "Example Brand",
                        "image_front_url": "https://images.openfoodfacts.org/products/example/front.jpg",
                        "quantity": "750 ml",
                    },
                }).encode("utf-8")

        urlopen.return_value = Response()

        self.assertEqual(external_lookup.lookup_barcode(BARCODE),
                         {
                             "name": "Example Wine",
                             "brand": "Example Brand",
                             "image_urls": ["https://images.openfoodfacts.org/products/example/front.jpg"],
                             "bottle_capacity_ml": Decimal("750.00"),
                             "source": "Open Food Facts",
                         })
        request = urlopen.call_args.args[0]
        self.assertIn(f"/api/v3/product/{BARCODE}?", request.full_url)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], external_lookup.LOOKUP_TIMEOUT_SECONDS)
        self.assertEqual(request.get_header("User-agent"), "WinesPOS/1.0 (https://github.com/nabiswa-cmd/POS)")

    @override_settings(OPEN_FOOD_FACTS_ENABLED=True)
    @patch("catalog.external_lookup.urllib.request.urlopen", side_effect=URLError("offline"))
    def test_network_failure_is_not_reported_as_a_missing_product(self, _urlopen):
        with self.assertRaises(external_lookup.ExternalLookupError):
            external_lookup.lookup_barcode(BARCODE)

    def test_invalid_gtin_is_not_sent_to_the_external_provider(self):
        self.assertTrue(external_lookup.is_supported_gtin(BARCODE))
        self.assertFalse(external_lookup.is_supported_gtin(BARCODE[:-1] + "4"))
        self.assertFalse(external_lookup.is_supported_gtin("UNKNOWN"))

    def test_product_image_download_requires_approved_host_and_validates_image(self):
        url = "https://images.barcodelookup.com/products/example.png"
        body = png_content("example.png").read()
        with patch("catalog.external_lookup.urllib.request.build_opener") as build_opener:
            build_opener.return_value.open.return_value = ImageResponse(url, body)
            image = external_lookup.download_product_image([url], BARCODE)

        self.assertEqual(image.name, f"external-{BARCODE}.png")
        build_opener.assert_called_once()
        build_opener.return_value.open.assert_called_once()

    def test_product_image_download_rejects_unapproved_hosts_without_requesting_them(self):
        with patch("catalog.external_lookup.urllib.request.build_opener") as build_opener:
            with self.assertRaises(external_lookup.ExternalImageError):
                external_lookup.download_product_image(["https://example.test/image.png"], BARCODE)

        build_opener.assert_not_called()


class ExternalProductImportTests(BaseCase):
    def test_local_scan_is_resolved_without_external_lookup(self):
        product = make_product()
        self.client.force_login(self.cashier)
        with patch("catalog.external_lookup.lookup_barcode") as lookup:
            response = self.post("/api/catalog/scan/", {"code": product.sku})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["item"]["product_id"], product.pk)
        lookup.assert_not_called()

    @override_settings(OPEN_FOOD_FACTS_ENABLED=True)
    @patch("catalog.external_lookup.lookup_barcode", return_value={"name": "Example Wine", "brand": "Example Brand"})
    def test_unknown_barcode_is_saved_inactive_for_manager_setup(self, lookup):
        self.client.force_login(self.cashier)

        response = self.post("/api/catalog/scan/", {"code": BARCODE})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["external_import"])
        self.assertTrue(response.json()["needs_setup"])
        self.assertIn("Saved inactive", response.json()["message"])
        product = Product.objects.get(name="Example Wine")
        self.assertFalse(product.active)
        self.assertEqual(str(product.selling_price), "0.00")
        self.assertEqual(product.stock_qty, 0)
        self.assertEqual(product.brand.name, "Example Brand")
        self.assertTrue(ProductCode.objects.filter(product=product, code=BARCODE, kind=ProductCode.KIND_BARCODE).exists())
        self.assertTrue(ProductCode.objects.filter(product=product, code=product.sku, kind=ProductCode.KIND_SKU).exists())
        lookup.assert_called_once_with(BARCODE)

        again = self.post("/api/catalog/scan/", {"code": BARCODE})
        self.assertEqual(again.status_code, 200)
        self.assertIn("already saved but inactive", again.json()["message"])
        self.assertEqual(lookup.call_count, 2)
        lookup.assert_called_with(BARCODE)
        self.assertEqual(Product.objects.filter(name="Example Wine").count(), 1)

    @override_settings(OPEN_FOOD_FACTS_ENABLED=True)
    @patch("catalog.external_lookup.lookup_barcode", return_value={"name": "Example Wine", "brand": "Example Brand"})
    def test_receiving_clerk_scan_saves_external_match_inactive_for_manager_setup(self, lookup):
        self.client.force_login(self.clerk)

        response = self.post("/api/catalog/scan/", {"code": BARCODE})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["needs_setup"])
        self.assertIn("Stock can still be received while inactive.", response.json()["message"])
        product = Product.objects.get(name="Example Wine")
        self.assertFalse(product.active)
        self.assertEqual(product.stock_qty, 0)
        self.assertTrue(ProductCode.objects.filter(product=product, code=BARCODE).exists())
        lookup.assert_called_once_with(BARCODE)

    @override_settings(OPEN_FOOD_FACTS_ENABLED=True)
    @patch("catalog.external_lookup.download_product_image")
    @patch("catalog.external_lookup.lookup_barcode", return_value={
        "name": "Example Wine",
        "brand": "Example Brand",
        "source": "Barcode Lookup",
        "image_urls": ["https://images.barcodelookup.com/example.jpg"],
        "bottle_capacity_ml": Decimal("750.00"),
    })
    def test_external_import_stores_image_and_bottle_capacity(self, lookup, download_image):
        self.client.force_login(self.cashier)
        download_image.return_value = png_content(f"external-{BARCODE}.png")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.post("/api/catalog/scan/", {"code": BARCODE})
            product = Product.objects.get(name="Example Wine")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(product.bottle_capacity_ml, Decimal("750.00"))
            self.assertTrue(product.image)
            self.assertTrue(Path(product.image.path).is_file())
            self.assertEqual(response.json()["item"]["image"], product.image.url)

        lookup.assert_called_once_with(BARCODE)
        download_image.assert_called_once_with(["https://images.barcodelookup.com/example.jpg"], BARCODE)

    @override_settings(OPEN_FOOD_FACTS_ENABLED=True)
    @patch("catalog.external_lookup.download_product_image",
           side_effect=external_lookup.ExternalImageError("The product image is invalid or unreadable."))
    @patch("catalog.external_lookup.lookup_barcode", return_value={
        "name": "Example Wine",
        "brand": "Example Brand",
        "image_urls": ["https://images.barcodelookup.com/example.jpg"],
        "bottle_capacity_ml": Decimal("750.00"),
    })
    def test_bad_image_does_not_block_product_import_and_is_reported(self, _lookup, _download_image):
        self.client.force_login(self.cashier)

        response = self.post("/api/catalog/scan/", {"code": BARCODE})
        product = Product.objects.get(name="Example Wine")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(product.image)
        self.assertEqual(product.bottle_capacity_ml, Decimal("750.00"))
        self.assertIn("Product image could not be saved", response.json()["message"])
        self.assertIn("Product image could not be saved", product.description)

    @override_settings(OPEN_FOOD_FACTS_ENABLED=True)
    @patch("catalog.external_lookup.download_product_image")
    @patch("catalog.external_lookup.lookup_barcode")
    def test_rescan_backfills_missing_metadata_without_overwriting_manager_capacity(self, lookup, download_image):
        lookup.side_effect = [
            {"name": "Example Wine", "brand": "Example Brand", "source": "Open Food Facts"},
            {
                "name": "Example Wine",
                "brand": "Example Brand",
                "source": "Barcode Lookup",
                "image_urls": ["https://images.barcodelookup.com/example.jpg"],
                "bottle_capacity_ml": Decimal("750.00"),
            },
        ]
        download_image.return_value = png_content(f"external-{BARCODE}.png")
        self.client.force_login(self.cashier)

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.post("/api/catalog/scan/", {"code": BARCODE})
            product = Product.objects.get(name="Example Wine")
            product.bottle_capacity_ml = Decimal("700.00")
            product.save(update_fields=["bottle_capacity_ml"])

            response = self.post("/api/catalog/scan/", {"code": BARCODE})
            product.refresh_from_db()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(product.bottle_capacity_ml, Decimal("700.00"))
            self.assertTrue(product.image)
            self.assertTrue(Path(product.image.path).is_file())

        self.assertEqual(lookup.call_count, 2)
        download_image.assert_called_once_with(["https://images.barcodelookup.com/example.jpg"], BARCODE)

    @override_settings(OPEN_FOOD_FACTS_ENABLED=False)
    def test_disabled_external_lookup_is_reported_clearly(self):
        self.client.force_login(self.cashier)

        response = self.post("/api/catalog/scan/", {"code": BARCODE})

        self.assertEqual(response.status_code, 503)
        self.assertIn("disabled", response.json()["error"])
        self.assertFalse(Product.objects.filter(name="Example Wine").exists())

    @override_settings(OPEN_FOOD_FACTS_ENABLED=True)
    @patch("catalog.external_lookup.lookup_barcode", return_value=None)
    def test_external_miss_is_distinguished_from_a_network_failure(self, _lookup):
        self.client.force_login(self.cashier)

        response = self.post("/api/catalog/scan/", {"code": BARCODE})

        self.assertEqual(response.status_code, 404)
        self.assertIn("online barcode databases", response.json()["error"])

    @override_settings(OPEN_FOOD_FACTS_ENABLED=True)
    @patch("catalog.external_lookup.lookup_barcode", return_value={"name": "Example Wine", "brand": "Example Brand"})
    def test_direct_cart_scan_imports_and_displays_match_without_selling_it(self, lookup):
        CashierShift.objects.create(till=Till.objects.get(number=1), cashier=self.cashier,
                                    authorized_by=self.supervisor, opening_float=0)
        self.client.force_login(self.cashier)
        session = self.client.session
        session["pos_till"] = 1
        session.save()

        response = self.post("/api/sales/cart/add/", {"code": BARCODE})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["external_import"])
        self.assertIn("Found online", response.json()["message"])
        self.assertEqual(response.json()["item"]["code"], BARCODE)
        self.assertFalse(Product.objects.get(name="Example Wine").active)
        lookup.assert_called_once_with(BARCODE)
