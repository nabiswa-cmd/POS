"""Free online sources are asked one after another; the first match wins and failures are handled kindly."""
import json
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.core.files.base import ContentFile
from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.tests import BaseCase
from catalog import external_lookup
from catalog.models import Product, ProductCode

BARCODE = "3017620422003"          # a valid EAN-13
UPC_A = "036000291452"             # a valid UPC-A


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body


def open_facts(code, name="Example Wine", brand="Example Brand"):
    return {
        "status": "success",
        "code": code,
        "product": {
            "code": code,
            "product_name": name,
            "brands": brand,
            "image_front_url": "https://images.openfoodfacts.org/products/example/front.jpg",
            "quantity": "750 ml",
        },
    }


def open_facts_miss():
    return {"status": "failure", "result": {"id": "product_not_found"}}


def upc_hit(code, title="Imported Whisky 750ml", brand="Glen Example"):
    return {"code": "OK", "total": 1, "items": [{"ean": code, "upc": code[1:] if code.startswith("0") else "",
                                                 "title": title, "brand": brand}]}


def barcode_lookup_hit(code, title="Imported Product", brand="Example Brand"):
    return {"products": [{
        "barcode_number": code,
        "title": title,
        "brand": brand,
        "images": ["https://images.barcodelookup.com/example.jpg"],
        "size": "750 ml",
    }]}


def route(responses):
    """urlopen stand-in: pick the answer by which website is being asked. A value may be an exception."""
    calls = []

    def fake(request, timeout=None):
        calls.append(request.full_url)
        for host, answer in responses.items():
            if host in request.full_url:
                if isinstance(answer, Exception):
                    raise answer
                return FakeResponse(answer)
        raise AssertionError("unexpected URL " + request.full_url)

    fake.calls = calls
    return fake


@override_settings(OPEN_FOOD_FACTS_ENABLED=True,
                   EXTERNAL_LOOKUP_PROVIDERS=["openfoodfacts", "openproductsfacts", "upcitemdb"])
class ProviderChainTests(TestCase):
    def setUp(self):
        cache.clear()

    def run_chain(self, responses, code=BARCODE):
        fake = route(responses)
        with patch("catalog.external_lookup.urllib.request.urlopen", fake):
            try:
                return external_lookup.lookup_barcode(code), fake.calls
            except external_lookup.ExternalLookupError as exc:
                return exc, fake.calls

    def test_first_source_wins_and_the_rest_are_not_asked(self):
        found, calls = self.run_chain({"openfoodfacts": open_facts(BARCODE)})
        self.assertEqual(found["source"], "Open Food Facts")
        self.assertEqual(len(calls), 1)

    def test_complete_metadata_is_cached_for_rescans(self):
        response = {"openfoodfacts": open_facts(BARCODE)}

        first, first_calls = self.run_chain(response)
        second, second_calls = self.run_chain(response)

        self.assertEqual(first["bottle_capacity_ml"], Decimal("750.00"))
        self.assertEqual(second, first)
        self.assertEqual(len(first_calls), 1)
        self.assertEqual(second_calls, [])

    def test_missing_image_or_capacity_is_reported(self):
        incomplete = open_facts(BARCODE)
        incomplete["product"].pop("image_front_url")
        incomplete["product"].pop("quantity")

        found, _ = self.run_chain({
            "openfoodfacts": incomplete,
            "openproductsfacts": open_facts_miss(),
            "upcitemdb": {"code": "OK", "total": 0, "items": []},
        })

        self.assertIn("product image or bottle capacity", found["metadata_warning"])

    def test_bottle_capacity_only_uses_explicit_metric_volume_units(self):
        self.assertEqual(external_lookup._bottle_capacity_ml("750 ml"), Decimal("750.00"))
        self.assertEqual(external_lookup._bottle_capacity_ml("75cl"), Decimal("750.00"))
        self.assertEqual(external_lookup._bottle_capacity_ml("0.75 L"), Decimal("750.00"))
        self.assertIsNone(external_lookup._bottle_capacity_ml("750 g", "Medium"))

    def test_falls_through_to_open_products_facts(self):
        found, calls = self.run_chain({"openfoodfacts": open_facts_miss(),
                                       "openproductsfacts": open_facts(BARCODE, "Kitchen Knife", "")})
        self.assertEqual(found["source"], "Open Products Facts")
        self.assertEqual(found["name"], "Kitchen Knife")
        self.assertEqual(len(calls), 2)

    def test_falls_through_to_upcitemdb_and_upca_matches_its_ean13_twin(self):
        found, calls = self.run_chain({"openfoodfacts": open_facts_miss(), "openproductsfacts": open_facts_miss(),
                                       "upcitemdb": upc_hit("0" + UPC_A)}, code=UPC_A)
        self.assertEqual(found["source"], "UPCitemdb")
        self.assertEqual(found["name"], "Imported Whisky 750ml")
        self.assertEqual(found["bottle_capacity_ml"], Decimal("750.00"))
        self.assertIn("upc=" + UPC_A, calls[-1])

    def test_all_miss_returns_none_and_each_miss_is_remembered(self):
        misses = {"openfoodfacts": open_facts_miss(), "openproductsfacts": open_facts_miss(),
                  "upcitemdb": {"code": "OK", "total": 0, "items": []}}
        found, calls = self.run_chain(misses)
        self.assertIsNone(found)
        self.assertEqual(len(calls), 3)
        found, calls = self.run_chain(misses)
        self.assertIsNone(found)
        self.assertEqual(calls, [])                      # all three remembered "not found"

    def test_http_404_counts_as_a_miss(self):
        err = HTTPError("u", 404, "nf", {}, None)
        found, _ = self.run_chain({"openfoodfacts": err, "openproductsfacts": err,
                                   "upcitemdb": upc_hit(BARCODE)})
        self.assertEqual(found["source"], "UPCitemdb")

    def test_a_limited_source_is_skipped_not_fatal(self):
        found, _ = self.run_chain({"openfoodfacts": HTTPError("u", 429, "slow", {}, None),
                                   "openproductsfacts": open_facts(BARCODE)})
        self.assertEqual(found["source"], "Open Products Facts")

    def test_upcitemdb_daily_limit_after_misses_is_a_miss_not_an_error(self):
        found, _ = self.run_chain({"openfoodfacts": open_facts_miss(), "openproductsfacts": open_facts_miss(),
                                   "upcitemdb": {"code": "EXCEED_LIMIT"}})
        self.assertIsNone(found)

    def test_error_is_raised_only_when_no_source_could_answer(self):
        err = HTTPError("u", 500, "boom", {}, None)
        found, _ = self.run_chain({"openfoodfacts": err, "openproductsfacts": err, "upcitemdb": {"code": "EXCEED_LIMIT"}})
        self.assertIsInstance(found, external_lookup.ExternalLookupError)

    def test_offline_stops_at_the_first_source(self):
        found, calls = self.run_chain({"openfoodfacts": URLError("offline"), "openproductsfacts": URLError("offline"),
                                       "upcitemdb": URLError("offline")})
        self.assertIsInstance(found, external_lookup.ExternalUnreachable)
        self.assertEqual(len(calls), 1)                  # no waiting 3 seconds on each of the others

    def test_a_reply_for_a_different_barcode_is_rejected_by_every_source(self):
        found, _ = self.run_chain({"openfoodfacts": open_facts("5449000000996"),
                                   "openproductsfacts": open_facts("5449000000996"),
                                   "upcitemdb": upc_hit("5449000000996")})
        self.assertIsInstance(found, external_lookup.ExternalLookupError)

    def test_nonsense_replies_are_errors_not_products(self):
        found, _ = self.run_chain({"openfoodfacts": ["not", "a", "dict"], "openproductsfacts": {"status": "weird"},
                                   "upcitemdb": {"code": "WHAT"}})
        self.assertIsInstance(found, external_lookup.ExternalLookupError)

    @override_settings(EXTERNAL_LOOKUP_PROVIDERS=["upcitemdb", "bogus", "UPCITEMDB"])
    def test_environment_chooses_sources_and_order_and_ignores_unknown_names(self):
        self.assertEqual(external_lookup.enabled_providers(), ["upcitemdb"])
        found, calls = self.run_chain({"upcitemdb": upc_hit(BARCODE)})
        self.assertEqual(found["source"], "UPCitemdb")
        self.assertEqual(len(calls), 1)

    @override_settings(EXTERNAL_LOOKUP_PROVIDERS=["barcodelookup"], BARCODE_LOOKUP_API_KEY="test-api-key")
    def test_barcode_lookup_uses_api_key_and_parses_matching_product(self):
        found, calls = self.run_chain({"api.barcodelookup.com": barcode_lookup_hit(
            BARCODE, title="  Example\nWine ", brand="Example Brand")})

        self.assertEqual(found, {
            "name": "Example Wine",
            "brand": "Example Brand",
            "image_urls": ["https://images.barcodelookup.com/example.jpg"],
            "bottle_capacity_ml": Decimal("750.00"),
            "source": "Barcode Lookup",
        })
        self.assertEqual(len(calls), 1)
        self.assertIn("barcode=" + BARCODE, calls[0])
        self.assertIn("key=test-api-key", calls[0])

    @override_settings(EXTERNAL_LOOKUP_PROVIDERS=["barcodelookup"], BARCODE_LOOKUP_API_KEY="test-api-key")
    def test_barcode_lookup_matches_zero_prefixed_upc_when_number_is_returned_as_integer(self):
        response = {
            "products": [{
                "barcode_number": 36000291452,
                "barcode_formats": "UPC-A 036000291452, EAN-13 0036000291452",
                "title": "Example Product",
                "brand": "Example Brand",
            }]
        }

        found, _ = self.run_chain({"api.barcodelookup.com": response}, code=UPC_A)

        self.assertEqual(found["name"], "Example Product")

    @override_settings(EXTERNAL_LOOKUP_PROVIDERS=["openfoodfacts", "barcodelookup"], BARCODE_LOOKUP_API_KEY="test-api-key")
    def test_later_source_fills_missing_image_and_capacity_without_replacing_name(self):
        first = open_facts(BARCODE)
        first["product"].pop("image_front_url")
        first["product"].pop("quantity")
        found, calls = self.run_chain({
            "openfoodfacts": first,
            "api.barcodelookup.com": barcode_lookup_hit(BARCODE, title="Different Online Title"),
        })

        self.assertEqual(found["name"], "Example Wine")
        self.assertEqual(found["source"], "Open Food Facts")
        self.assertEqual(found["image_urls"], ["https://images.barcodelookup.com/example.jpg"])
        self.assertEqual(found["bottle_capacity_ml"], Decimal("750.00"))
        self.assertEqual(len(calls), 2)

    @override_settings(EXTERNAL_LOOKUP_PROVIDERS=["barcodelookup"], BARCODE_LOOKUP_API_KEY="")
    def test_barcode_lookup_without_key_fails_before_network_request(self):
        with patch("catalog.external_lookup.urllib.request.urlopen") as request:
            with self.assertRaisesMessage(external_lookup.ExternalLookupError, "API key is not configured"):
                external_lookup.lookup_barcode(BARCODE)

        request.assert_not_called()

    @override_settings(EXTERNAL_LOOKUP_PROVIDERS=["openfoodfacts", "barcodelookup"], BARCODE_LOOKUP_API_KEY="")
    def test_missing_barcode_lookup_key_does_not_hide_free_provider_misses(self):
        found, calls = self.run_chain({"openfoodfacts": open_facts_miss()})

        self.assertIsNone(found)
        self.assertEqual(len(calls), 1)

    @override_settings(EXTERNAL_LOOKUP_PROVIDERS=["barcodelookup"], BARCODE_LOOKUP_API_KEY="test-api-key")
    def test_barcode_lookup_rejects_different_barcode(self):
        found, _ = self.run_chain({"api.barcodelookup.com": barcode_lookup_hit("5449000000996")})
        self.assertIsInstance(found, external_lookup.ExternalLookupError)

    @override_settings(EXTERNAL_LOOKUP_PROVIDERS=["barcodelookup"], BARCODE_LOOKUP_API_KEY="bad-api-key")
    def test_barcode_lookup_rejected_key_is_reported_without_echoing_key(self):
        error = HTTPError("https://api.barcodelookup.com/v3/products", 403, "invalid key", {}, None)
        found, _ = self.run_chain({"api.barcodelookup.com": error})

        self.assertIsInstance(found, external_lookup.ExternalLookupConfigurationError)
        self.assertIn("rejected the API key", str(found))
        self.assertNotIn("bad-api-key", str(found))

    @override_settings(OPEN_FOOD_FACTS_ENABLED=False)
    def test_master_switch_off_asks_nobody(self):
        found, calls = self.run_chain({})
        self.assertIsInstance(found, external_lookup.ExternalLookupDisabled)
        self.assertEqual(calls, [])

    def test_internal_and_invalid_codes_are_never_sent_out(self):
        found, calls = self.run_chain({}, code="TUSKER-500")
        self.assertIsNone(found)
        self.assertEqual(calls, [])
        found, calls = self.run_chain({}, code=BARCODE[:-1] + "4")
        self.assertIsNone(found)
        self.assertEqual(calls, [])

    def test_text_is_cleaned(self):
        found, _ = self.run_chain({"openfoodfacts": open_facts_miss(), "openproductsfacts": open_facts_miss(),
                                   "upcitemdb": upc_hit(BARCODE, title="  Fancy\n\tGin\x00 ", brand="A, B")})
        self.assertEqual(found["name"], "Fancy Gin")


@override_settings(OPEN_FOOD_FACTS_ENABLED=True)
class UnknownCodeScreenTests(BaseCase):
    """Cashier scans an unknown barcode: a manager gets a prefilled add link, a cashier gets plain guidance."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def scan(self, user):
        self.client.force_login(user)
        with patch("catalog.external_lookup.lookup_barcode", return_value=None):
            return self.post("/api/catalog/scan/", {"code": BARCODE})

    def test_cashier_is_told_to_ask_a_manager_and_gets_no_link(self):
        res = self.scan(self.cashier)
        self.assertEqual(res.status_code, 404)
        self.assertIn("online barcode databases", res.json()["error"])
        self.assertIn("Ask a manager", res.json()["error"])
        self.assertNotIn("add_url", res.json())

    def test_manager_gets_a_link_with_the_barcode_filled_in(self):
        res = self.scan(self.manager)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["add_url"], "/manage/products/new/?barcode=" + BARCODE)

    def test_internal_codes_keep_the_plain_message(self):
        self.client.force_login(self.cashier)
        res = self.post("/api/catalog/scan/", {"code": "NOPE-1"})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "Code not found.")

    def test_prefilled_form_shows_the_code_and_saves_it_with_the_product(self):
        self.client.force_login(self.manager)
        page = self.client.get("/manage/products/new/", {"barcode": BARCODE, "name": "Example Wine"})
        self.assertEqual(page.status_code, 200)
        self.assertIn(BARCODE, page.content.decode())
        self.assertEqual(page.context["form"].initial["name"], "Example Wine")

        form = {f.name: (f.initial if f.initial is not None else "") for f in page.context["form"]}
        form.update({"name": "Example Wine", "selling_price": "1500", "buying_cost": "900", "active": "on"})
        res = self.client.post("/manage/products/new/?barcode=" + BARCODE, {k: v for k, v in form.items() if v != ""})
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["form"].errors)
        product = Product.objects.get(name="Example Wine")
        self.assertTrue(ProductCode.objects.filter(product=product, code=BARCODE).exists())

    @patch("catalog.external_lookup.lookup_barcode", return_value={
        "name": "Example Wine",
        "brand": "Example Brand",
        "bottle_capacity_ml": Decimal("750.00"),
        "image_urls": ["https://images.barcodelookup.com/example.jpg"],
        "source": "Barcode Lookup",
    })
    def test_product_lookup_api_returns_fields_without_creating_a_product(self, _lookup):
        self.client.force_login(self.manager)

        response = self.post("/api/catalog/products/lookup-online/", {"code": BARCODE})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["product"]["name"], "Example Wine")
        self.assertEqual(response.json()["product"]["brand"], "Example Brand")
        self.assertEqual(response.json()["product"]["bottle_capacity_ml"], "750.00")
        self.assertTrue(response.json()["product"]["image_available"])
        self.assertFalse(Product.objects.exists())

    def test_product_lookup_api_is_manager_only(self):
        self.client.force_login(self.cashier)

        response = self.post("/api/catalog/products/lookup-online/", {"code": BARCODE})

        self.assertEqual(response.status_code, 403)

    @patch("catalog.external_lookup.download_product_image",
           return_value=ContentFile(b"online-image", name=f"external-{BARCODE}.png"))
    @patch("catalog.external_lookup.lookup_barcode", return_value={
        "name": "Example Wine",
        "brand": "Example Brand",
        "bottle_capacity_ml": Decimal("750.00"),
        "image_urls": ["https://images.barcodelookup.com/example.jpg"],
        "source": "Barcode Lookup",
    })
    def test_product_form_saves_lookup_fields_barcode_photo_and_adds_another(self, lookup, _download_image):
        self.client.force_login(self.manager)
        page = self.client.get("/manage/products/new/")
        form_data = {field.name: (field.initial if field.initial is not None else "")
                     for field in page.context["form"]}
        form_data.update({
            "name": "Example Wine",
            "online_brand": "Example Brand",
            "barcode": BARCODE,
            "online_lookup_done": "1",
            "bottle_capacity_ml": "750",
            "selling_price": "1500",
            "buying_cost": "900",
            "active": "on",
            "save_and_add_another": "1",
        })

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post("/manage/products/new/", form_data, follow=True)
            product = Product.objects.get(name="Example Wine")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.redirect_chain[-1][0], "/manage/products/new/")
            self.assertEqual(product.brand.name, "Example Brand")
            self.assertEqual(product.bottle_capacity_ml, Decimal("750"))
            self.assertTrue(ProductCode.objects.filter(product=product, code=BARCODE).exists())
            self.assertTrue(product.image)
            self.assertTrue(product.image.storage.exists(product.image.name))

        lookup.assert_called_once_with(BARCODE)

    def test_a_barcode_already_in_use_blocks_the_save_and_leaves_no_half_product(self):
        from catalog.tests import make_product
        make_product("Existing", "EX-1").codes.create(code=BARCODE, kind=ProductCode.KIND_BARCODE)
        self.client.force_login(self.manager)
        res = self.client.post("/manage/products/new/?barcode=" + BARCODE,
                               {"name": "Dup", "selling_price": "100", "buying_cost": "50", "active": "on"})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Dup").exists())
