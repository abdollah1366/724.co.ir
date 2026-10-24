"""پشتیبان‌گیری از پایگاه داده با بررسی صحت و حذف نسخه‌های قدیمی.

نمونه (روزانه با Task Scheduler ویندوز یا cron):
    python manage.py backup_db --keep-days 30
"""

import datetime
import gzip
import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.audit import log
from apps.audit.models import AuditLog


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Command(BaseCommand):
    help = "پشتیبان فشرده از پایگاه داده می‌گیرد، صحت آن را بررسی و نسخه‌های قدیمی را حذف می‌کند."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=getattr(settings, "BACKUP_DIR", None))
        parser.add_argument("--keep-days", type=int, default=30)

    def handle(self, *args, **options):
        target = Path(options["dir"] or Path(settings.BASE_DIR) / "backups")
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        vendor = connection.vendor
        if vendor == "sqlite":
            path = self._sqlite(target, stamp)
        elif vendor == "postgresql":
            path = self._postgres(target, stamp)
        elif vendor == "mysql":
            path = self._mysql(target, stamp)
        else:
            raise CommandError(f"پایگاه داده {vendor} پشتیبانی نمی‌شود.")
        checksum = sha256(path)
        Path(f"{path}.sha256").write_text(f"{checksum}  {path.name}\n", encoding="utf-8")
        files = self._private_files(target, stamp)
        if files:
            Path(f"{files}.sha256").write_text(f"{sha256(files)}  {files.name}\n", encoding="utf-8")
            self.stdout.write(f"پشتیبان مدارک بیماران: {files}")
        removed = self._cleanup(target, options["keep_days"])
        log.record(AuditLog.Action.BACKUP, message=f"{path.name} ({path.stat().st_size} بایت، sha256={checksum[:16]})")
        self.stdout.write(self.style.SUCCESS(f"پشتیبان ساخته شد: {path}"))
        if removed:
            self.stdout.write(f"{removed} پشتیبان قدیمی حذف شد.")

    def _sqlite(self, target, stamp):
        db_path = connection.settings_dict["NAME"]
        out = target / f"db-{stamp}.sqlite3.gz"
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "db.sqlite3"
            # API پشتیبان SQLite نسخه‌ای سازگار حتی هنگام کار سایت می‌سازد.
            src = sqlite3.connect(str(db_path), uri=str(db_path).startswith("file:"))
            dst = sqlite3.connect(raw)
            with dst:
                src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
            src.close()
            dst.close()
            if result != "ok":
                raise CommandError(f"بررسی صحت پشتیبان ناموفق بود: {result}")
            with open(raw, "rb") as fin, gzip.open(out, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        return out

    def _postgres(self, target, stamp):
        db = connection.settings_dict
        out = target / f"db-{stamp}.dump"
        env = {**os.environ, "PGPASSWORD": db.get("PASSWORD") or ""}
        cmd = ["pg_dump", "-Fc", "-h", db.get("HOST") or "localhost", "-p", str(db.get("PORT") or 5432),
               "-U", db.get("USER") or "", "-f", str(out), db["NAME"]]
        try:
            subprocess.run(cmd, env=env, check=True)
            subprocess.run(["pg_restore", "--list", str(out)], check=True, stdout=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CommandError(f"pg_dump/pg_restore ناموفق بود: {exc}") from exc
        return out

    def _mysql(self, target, stamp):
        """MySQL/MariaDB (هاست اشتراکی): mysqldump فشرده؛ پایان کامل فایل بررسی می‌شود."""
        db = connection.settings_dict
        out = target / f"db-{stamp}.sql.gz"
        env = {**os.environ, "MYSQL_PWD": db.get("PASSWORD") or ""}
        host = db.get("HOST") or "localhost"
        server = ["--socket", host] if host.startswith("/") else ["-h", host, "-P", str(db.get("PORT") or 3306)]
        cmd = ["mysqldump", "--single-transaction", "--quick", "--routines", "--default-character-set=utf8mb4",
               *server, "-u", db.get("USER") or "", db["NAME"]]
        try:
            with gzip.open(out, "wb") as fout:
                proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE)
                shutil.copyfileobj(proc.stdout, fout)
                if proc.wait() != 0:
                    raise subprocess.CalledProcessError(proc.returncode, cmd[0])
        except (OSError, subprocess.CalledProcessError) as exc:
            out.unlink(missing_ok=True)
            raise CommandError(f"mysqldump ناموفق بود: {exc}") from exc
        # خواندن کامل، CRC فایل gzip را هم بررسی می‌کند.
        tail = b""
        with gzip.open(out, "rb") as fin:
            for chunk in iter(lambda: fin.read(1024 * 1024), b""):
                tail = (tail + chunk)[-400:]
        if b"Dump completed" not in tail:
            out.unlink(missing_ok=True)
            raise CommandError("پشتیبان MySQL ناقص است.")
        return out

    def _private_files(self, target, stamp):
        """مدارک اسکن‌شده بیماران (پوشه خصوصی) در یک فایل zip کنار پشتیبان پایگاه داده."""
        root = Path(getattr(settings, "PRIVATE_MEDIA_ROOT", ""))
        if not root.is_dir() or not any(p.is_file() for p in root.rglob("*")):
            return None
        out = target / f"files-{stamp}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in root.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(root))
        with zipfile.ZipFile(out) as archive:
            if archive.testzip() is not None:
                raise CommandError("بررسی صحت پشتیبان مدارک ناموفق بود.")
        return out

    def _cleanup(self, target, keep_days):
        cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
        removed = 0
        for path in [*target.glob("db-*"), *target.glob("files-*")]:
            if datetime.datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
                removed += 1
        return removed
