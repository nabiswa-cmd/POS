"""The logo and tab icon appear on the sign-in screens, the manager screens and the admin."""
from django.core.management import call_command

from accounts.tests import BaseCase


class LogoPageTests(BaseCase):
    def test_sign_in_screens_show_the_small_logo_and_the_tab_icon(self):
        call_command("setup_shop", verbosity=0)
        for mode in ("pos", "manager", "receiving"):
            page = self.client.get(f"/app/{mode}/")
            self.assertContains(page, 'class="auth-logo"')
            self.assertContains(page, "core/logo-mark.png")
            self.assertNotContains(page, "core/logo.png")
            self.assertContains(page, "core/favicon.ico")

    def test_manager_pages_show_the_header_logo_and_the_tab_icon(self):
        call_command("setup_shop", verbosity=0)
        self.client.force_login(self.manager)
        page = self.client.get("/manage/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'class="brand-mark"')
        self.assertContains(page, "core/logo-mark.png")
        self.assertContains(page, "core/favicon.ico")

    def test_admin_shows_the_logo(self):
        call_command("setup_shop", verbosity=0)
        self.manager.is_staff = True
        self.manager.save()
        self.client.force_login(self.manager)
        page = self.client.get("/admin/")
        self.assertContains(page, "core/logo-mark.png")
        self.assertContains(page, "core/favicon.ico")
