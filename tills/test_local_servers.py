from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from accounts.tests import BaseCase
from core import runtime
from tills.forms import TillLocalServersForm
from tills.local_servers import local_server_hosts, normalize_local_server_urls
from tills.models import CashierShift, Till


class LocalServerUrlTests(TestCase):
    def test_normalizes_and_deduplicates_private_ipv4_urls(self):
        self.assertEqual(
            normalize_local_server_urls(
                "http://192.168.1.10:8000/\nhttp://192.168.1.10:8000\nhttps://10.0.0.4"
            ),
            ["http://192.168.1.10:8000", "https://10.0.0.4:443"],
        )

    def test_refuses_public_hosts_and_non_origin_urls(self):
        for value in ("http://8.8.8.8:8000", "http://127.0.0.1:8000",
                      "http://192.168.1.10:8000/manage/", "http://user@192.168.1.10:8000",
                      "http://192.168.1.10:70000", "http://198.18.0.1:8000",
                      "http://[invalid:8000"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_local_server_urls(value)

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_local_server_hosts_are_added_to_django_allowed_hosts(self):
        with mock.patch.dict(runtime._defaults, {"EXTRA_ALLOWED_HOSTS": ["testserver"]}), \
                mock.patch("tills.local_servers.local_server_hosts", return_value=["192.168.1.10"]):
            runtime._apply_local_till_hosts({"EXTRA_ALLOWED_HOSTS": "pos.example.test"})
        self.assertEqual(set(runtime.settings.ALLOWED_HOSTS), {"testserver", "pos.example.test", "192.168.1.10"})


class TillLocalConnectionPageTests(BaseCase):
    def test_manager_can_save_urls_and_invalid_urls_are_rejected(self):
        self.client.force_login(self.manager)
        till = Till.objects.get(number=1)

        with mock.patch("tills.views.runtime.apply"):
            response = self.client.post("/manage/till-connections/", {
                "till_number": 1, "local_server_urls": "http://192.168.1.10:8000\nhttp://10.0.0.4:8000",
            })
        self.assertRedirects(response, "/manage/till-connections/")
        till.refresh_from_db()
        self.assertEqual(till.local_server_urls, ["http://192.168.1.10:8000", "http://10.0.0.4:8000"])
        self.assertIn("192.168.1.10", local_server_hosts())

        with mock.patch("tills.views.runtime.apply"):
            response = self.client.post("/manage/till-connections/", {
                "till_number": 1, "local_server_urls": "http://8.8.8.8:8000",
            })
        self.assertRedirects(response, "/manage/till-connections/")
        till.refresh_from_db()
        self.assertEqual(till.local_server_urls, ["http://192.168.1.10:8000", "http://10.0.0.4:8000"])

    def test_cashier_can_retrieve_fallback_urls_only_for_their_open_till(self):
        till = Till.objects.get(number=1)
        till.local_server_urls = ["http://192.168.1.10:8000"]
        till.save(update_fields=["local_server_urls"])
        CashierShift.objects.create(
            till=till, cashier=self.cashier, authorized_by=self.manager, opening_float="0")
        self.client.force_login(self.cashier)
        session = self.client.session
        session["pos_till"] = till.number
        session.save()

        response = self.client.get("/api/pos/local-fallback/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["urls"], ["http://192.168.1.10:8000"])


class PwaEndpointTests(TestCase):
    def test_offline_page_and_root_scoped_service_worker_are_served(self):
        self.assertEqual(self.client.get("/offline/").status_code, 200)
        response = self.client.get("/service-worker.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertIn("caches.match(OFFLINE_URL)", response.content.decode())

    def test_pos_login_has_installable_pwa_metadata(self):
        response = self.client.get("/app/pos/")
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, 'href="/manifest.webmanifest"')
        self.assertContains(response, "data-pwa-install")
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest["Content-Type"], "application/manifest+json")
        self.assertIn("/static/core/icons/icon-192.png", manifest.content.decode())
