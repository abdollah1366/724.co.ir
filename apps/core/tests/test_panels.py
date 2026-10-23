from django.test import TestCase
from django.urls import reverse

from apps.core.panels import PANELS, panels_for
from apps.inventory.tests.fixtures import staff


def labels(user):
    return {label for _, links in panels_for(user) for label, _ in links}


class PanelLinksTests(TestCase):
    def test_superuser_sees_every_panel_on_admin_index(self):
        root = staff("root", superuser=True)
        self.client.force_login(root)
        page = self.client.get(reverse("admin:index"))
        self.assertContains(page, "پنل‌های کاری")
        for _, links in PANELS:
            for label, name, _ in links:
                self.assertContains(page, reverse(name))

    def test_links_follow_role_permissions(self):
        cashier = labels(staff("cashier", "صندوق‌دار"))
        self.assertIn("صندوق فروش", cashier)
        self.assertNotIn("کارتابل جواب‌ها", cashier)
        self.assertNotIn("بیماران من (میز کار پزشک)", cashier)

        author = labels(staff("author", "نویسنده محتوا"))
        self.assertIn("پیشخوان وبلاگ", author)
        self.assertNotIn("داشبورد داروخانه", author)
        self.assertNotIn("کارکنان و نقش‌ها", author)

        self.assertIn("کارکنان و نقش‌ها", labels(staff("blogman", "مدیر وبلاگ")))
        lab = labels(staff("labtech", "کارشناس آزمایشگاه"))
        self.assertIn("کارتابل جواب‌ها", lab)
        self.assertNotIn("پذیرش", lab)

    def test_every_shown_link_opens_for_that_user(self):
        for role in ("صندوق‌دار", "نویسنده محتوا", "کارشناس آزمایشگاه", "پذیرش درمانگاه", "مدیر کل", "پزشک"):
            user = staff(f"u-{len(role)}-{role[:3]}", role)
            self.client.force_login(user)
            for _, links in panels_for(user):
                for label, url in links:
                    if "display" in url:
                        continue  # نمایشگر صف صفحه تمام‌صفحه عمومی است
                    status = self.client.get(url).status_code
                    self.assertIn(status, (200, 302), f"{role}: {label} {url} → {status}")
