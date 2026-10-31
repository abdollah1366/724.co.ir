"""هر نقشی که می‌تواند سندی را بسازد یا ویرایش کند، باید بتواند ردیف‌های آن را هم وارد کند؛ وگرنه بخش ردیف‌ها در فرم پنهان می‌ماند
و ثبت نهایی با «سند ردیف ندارد» شکست می‌خورد (گزارش کاربر برای رسید انبار با نقش مدیر داروخانه)."""

from django.contrib import admin
from django.test import TestCase

from apps.audit.admin_utils import LockedInlineMixin
from apps.audit.roles import ROLES, role_permissions


class RoleInlinePermissionTests(TestCase):
    def test_roles_that_add_documents_can_add_their_lines(self):
        problems = []
        for model, model_admin in admin.site._registry.items():
            parent = f"{model._meta.app_label}.add_{model._meta.model_name}"
            for inline in model_admin.inlines:
                if not issubclass(inline, LockedInlineMixin):
                    continue
                line = f"{inline.model._meta.app_label}.add_{inline.model._meta.model_name}"
                for role, spec in ROLES.items():
                    perms = set(role_permissions(spec))
                    if parent in perms and line not in perms:
                        problems.append(f"{role}: {parent} بدون {line}")
        self.assertEqual(problems, [])

    def test_pharmacy_manager_sees_receipt_rows_with_one_blank_row(self):
        from django.urls import reverse

        from apps.inventory.tests.fixtures import staff

        self.client.force_login(staff("pm", "مدیر داروخانه"))
        page = self.client.get(reverse("admin:purchasing_goodsreceipt_add"))
        self.assertContains(page, 'name="lines-TOTAL_FORMS" value="1"')
