from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.core.models import DailyVisit, MenuUsage
from apps.core.panels import QUICK_COUNT, QUICK_DEFAULTS, menu_for, quick_links
from apps.core.stats import STATS_CACHE_KEY, VISIT_COOKIE
from apps.inventory.tests.fixtures import staff

BROWSER = {"HTTP_USER_AGENT": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36"}


class QuickAccessTests(TestCase):
    def test_superuser_gets_the_seven_defaults_in_order(self):
        root = staff("root", superuser=True)
        names = [item["name"] for item in quick_links(root)]
        self.assertEqual(names, QUICK_DEFAULTS)
        self.assertEqual(len(names), QUICK_COUNT)

    def test_only_permitted_links_and_most_used_first(self):
        cashier = staff("cashier", "صندوق‌دار")
        allowed = {link["name"] for group in menu_for(cashier) for link in group["links"]}
        quick = [item["name"] for item in quick_links(cashier)]
        self.assertTrue(set(quick) <= allowed)
        self.assertNotIn("lab:staff_worklist", quick)
        self.assertLessEqual(len(quick), QUICK_COUNT)

        favourite = quick[-1]
        self.assertNotEqual(quick[0], favourite)
        self.client.force_login(cashier)
        for _ in range(3):
            self.assertEqual(self.client.get(reverse(favourite)).status_code, 200)
        self.assertEqual(MenuUsage.objects.get(user=cashier, url_name=favourite).count, 3)
        self.assertEqual(quick_links(cashier)[0]["name"], favourite)

    def test_site_home_and_queue_displays_never_take_a_quick_slot(self):
        root = staff("root2", superuser=True)
        self.client.force_login(root)
        self.client.get(reverse("core:home"))
        self.assertFalse(MenuUsage.objects.filter(url_name="core:home").exists())
        MenuUsage.objects.create(user=root, url_name="ehr:queue_display", count=50)
        self.assertNotIn("ehr:queue_display", [i["name"] for i in quick_links(root)])

    def test_active_link_is_the_longest_matching_prefix(self):
        root = staff("root3", superuser=True)
        groups = menu_for(root, reverse("backoffice:stock") + "?q=x")
        active = [(g["title"], link["name"]) for g in groups for link in g["links"] if link.get("active")]
        self.assertEqual(active, [("داروخانه و تجهیزات", "backoffice:stock")])
        self.assertTrue(next(g for g in groups if g["title"] == "داروخانه و تجهیزات")["active"])


class StaffShellTests(TestCase):
    def test_staff_home_needs_staff_login(self):
        page = self.client.get(reverse("core:staff_home"))
        self.assertEqual(page.status_code, 302)

    def test_staff_home_shows_quick_access_visits_and_panels(self):
        DailyVisit.objects.create(date="2026-01-01", page_views=1234, visitors=200)
        self.client.force_login(staff("root", superuser=True))
        page = self.client.get(reverse("core:staff_home"))
        self.assertContains(page, "دسترسی سریع")
        self.assertContains(page, "آمار بازدید سایت")
        self.assertContains(page, "۱٬۲۳۴")
        self.assertContains(page, reverse("finance:cashier"))
        self.assertContains(page, 'class="sb"')

    def test_module_pages_use_the_staff_shell_with_breadcrumb(self):
        self.client.force_login(staff("root", superuser=True))
        page = self.client.get(reverse("backoffice:stock"))
        self.assertContains(page, 'class="sb"')
        self.assertContains(page, 'aria-current="page"')
        self.assertContains(page, "<strong>موجودی انبار</strong>", html=False)
        self.assertNotContains(page, "site-footer")

    def test_role_without_permissions_sees_only_allowed_menus(self):
        author = staff("author", "نویسنده محتوا")
        self.client.force_login(author)
        page = self.client.get(reverse("core:staff_home"))
        self.assertContains(page, reverse("blog:manage"))
        self.assertNotContains(page, reverse("backoffice:pos"))
        self.assertNotContains(page, "نوبت‌های امروز")

    def test_admin_index_has_quick_access_and_theme(self):
        self.client.force_login(staff("root", superuser=True))
        page = self.client.get(reverse("admin:index"))
        self.assertContains(page, "دسترسی سریع")
        self.assertContains(page, "css/admin_theme.css")
        self.assertContains(page, reverse("core:staff_home"))


class VisitCounterTests(TestCase):
    def setUp(self):
        cache.delete(STATS_CACHE_KEY)

    def test_page_views_and_daily_visitors_are_counted_without_ip(self):
        self.client.get(reverse("core:home"), **BROWSER)
        self.assertIn(VISIT_COOKIE, self.client.cookies)
        self.client.get(reverse("core:about"), **BROWSER)
        day = DailyVisit.objects.get()
        self.assertEqual((day.page_views, day.visitors), (2, 1))
        self.assertEqual([f.name for f in DailyVisit._meta.fields], ["id", "date", "page_views", "visitors"])

    def test_bots_staff_admin_and_errors_are_not_counted(self):
        self.client.get(reverse("core:home"), HTTP_USER_AGENT="Googlebot/2.1")
        self.client.get(reverse("core:home"))  # بدون User-Agent
        self.client.get("/no-such-page/", **BROWSER)
        self.client.get(reverse("admin:login"), **BROWSER)
        self.client.force_login(staff("clerk", "صندوق‌دار"))
        self.client.get(reverse("core:home"), **BROWSER)
        self.assertFalse(DailyVisit.objects.exists())

    def test_home_shows_stats_and_footer_counter(self):
        DailyVisit.objects.create(date="2026-01-01", page_views=5000, visitors=900)
        page = self.client.get(reverse("core:home"), **BROWSER)
        self.assertContains(page, "بازدید از سایت")
        self.assertContains(page, "۵٬۰۰۰")
        self.assertContains(page, "کل بازدیدها")
