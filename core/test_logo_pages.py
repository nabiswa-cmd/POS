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

    def test_sign_in_theme_control_is_a_small_icon_button(self):
        page = self.client.get("/app/manager/")
        self.assertContains(page, 'class="theme-toggle floating"')
        self.assertContains(page, 'type="password"')
        self.assertContains(page, 'id="l-secret-label"')
        self.assertContains(page, "Manager / supervisor password or PIN")
        self.assertContains(page, 'aria-label="Switch to light mode"')
        self.assertNotContains(page, 'class="t-label"')
        self.assertLess(page.content.index(b'<main class="auth">'),
                        page.content.index(b'class="theme-toggle floating"'))

    def test_manager_pages_show_the_header_logo_and_the_tab_icon(self):
        call_command("setup_shop", verbosity=0)
        self.client.force_login(self.manager)
        page = self.client.get("/manage/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'class="brand-mark"')
        self.assertContains(page, "core/logo-mark.png")
        self.assertContains(page, "core/favicon.ico")
        self.assertContains(page, 'class="sidebar-brand"')
        self.assertContains(page, 'id="nav-close"')

    def test_mobile_quick_nav_marks_the_current_products_or_stock_section(self):
        call_command("setup_shop", verbosity=0)
        self.client.force_login(self.manager)

        products = self.client.get("/manage/products/")
        self.assertContains(products, '<a href="/manage/products/" class="on" aria-current="page">')

        stock = self.client.get("/manage/stock/?low=1")
        self.assertContains(stock, '<a href="/manage/stock/?low=1" class="on" aria-current="page">')

    def test_manager_dashboard_keeps_alerts_and_manager_shortcuts_visible(self):
        call_command("setup_shop", verbosity=0)
        self.client.force_login(self.manager)

        page = self.client.get("/manage/")

        self.assertContains(page, "Your shop at a glance.")
        self.assertContains(page, "Needs attention")
        self.assertContains(page, "Staff, roles &amp; settings")
        self.assertContains(page, 'href="/manage/charts/"')

    def test_admin_shows_the_logo(self):
        call_command("setup_shop", verbosity=0)
        self.manager.is_staff = True
        self.manager.save()
        self.client.force_login(self.manager)
        page = self.client.get("/admin/")
        self.assertContains(page, "core/logo-mark.png")
        self.assertContains(page, "core/favicon.ico")
        self.assertContains(page, "core/admin-nav.js")
