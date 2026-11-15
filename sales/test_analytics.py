from datetime import timedelta
from decimal import Decimal as D

from django.test import Client
from django.utils import timezone

from . import analytics, services
from .tests import SalesCase


class AnalyticsTests(SalesCase):
    def sell(self, qty):
        self.scan(qty=qty)
        return services.checkout_cash(self.cashier, self.shift, str(3500 * qty))

    def test_totals_best_sellers_and_not_selling(self):
        self.sell(2)
        self.sell(1)
        today = timezone.localdate()
        d = analytics.build(today - timedelta(days=2), today)
        self.assertEqual(len(d["daily"]), 3)
        self.assertEqual(d["daily"][-1]["total"], 10500.0)
        self.assertEqual(d["daily"][-1]["count"], 2)
        self.assertEqual(d["kpi"]["count"], 2)
        self.assertEqual(d["kpi"]["average"], 5250.0)
        top = d["top_revenue"][0]
        self.assertEqual((top["name"], top["revenue"], top["bottles"]), (self.product.name, 10500.0, 3))
        self.assertEqual(d["top_units"][0]["bottles"], 3)
        self.assertEqual(d["payments"][0]["label"], "Cash")
        self.assertEqual(d["payments"][0]["total"], 10500.0)
        self.assertEqual(sum(h["count"] for h in d["hourly"]), 2)
        self.assertEqual(d["not_selling"], [])
        d2 = analytics.build(today - timedelta(days=9), today - timedelta(days=5))
        self.assertEqual(d2["kpi"]["total"], 0)
        self.assertEqual([r["name"] for r in d2["not_selling"]], [self.product.name])

    def test_screen_and_json_need_the_report_permission(self):
        self.sell(1)
        for url in ("/manage/charts/", "/manage/charts/data/"):
            c = Client()
            self.assertEqual(c.get(url).status_code, 302)
            c.force_login(self.cashier)
            self.assertEqual(c.get(url).status_code, 403)
            c.force_login(self.manager)
            self.assertEqual(c.get(url).status_code, 200)
        data = Client()
        data.force_login(self.manager)
        self.assertEqual(data.get("/manage/charts/data/").json()["kpi"]["count"], 1)
