import gzip
import sqlite3
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from apps.audit import roles
from apps.audit.context import acting_as
from apps.audit.documents import next_number
from apps.audit.models import AuditLog
from apps.inventory.tests.fixtures import drug, receive, staff
from apps.shop.models import PriceHistory


class AuditLogTests(TestCase):
    def test_changes_are_logged_with_user_and_diff(self):
        user = staff("manager", "مدیر داروخانه")
        with acting_as(user, "10.0.0.1"):
            product = drug(price=100_000)
            product.price = 120_000
            product.save()
        entry = AuditLog.objects.filter(model="shop.product", action=AuditLog.Action.UPDATE).get()
        self.assertEqual(entry.user, user)
        self.assertEqual(entry.ip, "10.0.0.1")
        self.assertEqual(entry.changes["price"], [100_000, 120_000])
        self.assertEqual(list(PriceHistory.objects.values_list("previous_price", "price", "changed_by")),
                         [(100_000, 120_000, user.pk), (None, 100_000, user.pk)])

    def test_audit_log_cannot_be_edited_or_deleted(self):
        entry = AuditLog.objects.create(action=AuditLog.Action.EVENT, message="x")
        entry.message = "y"
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_posting_is_logged(self):
        user = staff("keeper", "انباردار")
        receive(drug(), 1, 100, user=user)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.POST, user=user, model="purchasing.goodsreceipt").exists())

    def test_login_success_and_failure_logged_without_password(self):
        staff("09121234567")
        self.client.post(reverse("accounts:login"), {"username": "09121234567", "password": "wrong-password"})
        self.client.post(reverse("accounts:login"), {"username": "09121234567", "password": "x-strong-pass-1"})
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.LOGIN).exists())
        self.assertFalse(AuditLog.objects.filter(message__contains="wrong-password").exists())

    def test_document_numbers_are_sequential_and_unique(self):
        first, second = next_number("TST"), next_number("TST")
        self.assertNotEqual(first, second)
        self.assertEqual(int(second[-5:]), int(first[-5:]) + 1)


class RoleTests(TestCase):
    def test_all_role_permissions_exist(self):
        roles.setup_roles()  # خطا می‌دهد اگر مجوزی وجود نداشته باشد
        self.assertEqual(Group.objects.filter(name__in=roles.ROLES).count(), len(roles.ROLES))

    def test_role_separation(self):
        cashier = staff("cashier", "صندوق‌دار")
        pharmacist = staff("pharm", "داروساز مسئول فنی")
        accountant = staff("acc", "حسابدار")
        self.assertTrue(cashier.has_perm("sales.use_pos"))
        self.assertFalse(cashier.has_perm("sales.approve_prescription"))
        self.assertFalse(cashier.has_perm("purchasing.post_goodsreceipt"))
        self.assertFalse(cashier.has_perm("accounting.view_financial_reports"))
        self.assertTrue(pharmacist.has_perm("sales.approve_prescription"))
        self.assertTrue(accountant.has_perm("accounting.view_financial_reports"))
        self.assertFalse(accountant.has_perm("inventory.post_inventorydocument"))
        self.assertFalse(cashier.has_perm("audit.delete_auditlog"))


@skipUnless(connection.vendor == "sqlite", "پشتیبان‌گیری SQLite")
class BackupTests(TestCase):
    def test_backup_creates_verified_compressed_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            call_command("backup_db", dir=tmp, stdout=open("/dev/null", "w"))
            files = list(Path(tmp).glob("db-*.sqlite3.gz"))
            self.assertEqual(len(files), 1)
            self.assertTrue(Path(f"{files[0]}.sha256").exists())
            raw = Path(tmp) / "restored.sqlite3"
            raw.write_bytes(gzip.decompress(files[0].read_bytes()))
            db = sqlite3.connect(raw)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            db.close()
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.BACKUP).exists())
