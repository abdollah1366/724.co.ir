#!/bin/sh
# کارهای زمان‌بندی‌شده. روی VPS با systemd (724-task@.service) و روی هاست cPanel با Cron Jobs اجرا می‌شود.
# محیط پایتون در پوشه venv کنار پوشه کد است (/srv/724/venv یا ~/724/venv).
set -eu
umask 077
APP=$(cd "$(dirname "$0")/.." && pwd)
cd "$APP"
manage() { "$APP/../venv/bin/python" manage.py "$@"; }

case "${1:-}" in
    backup)
        manage backup_db --keep-days "${BACKUP_KEEP_DAYS:-30}"
        ;;
    nightly)
        manage expire_batches
        manage clearsessions
        ;;
    reminders)
        manage ehr_reminders
        manage lab_reminders
        ;;
    telemed)
        manage telemed_tick
        ;;
    events)
        # تلاش مجدد رویدادهای بین‌ماژولی ناموفق یا معوق (outbox)؛ پردازش عادی بلافاصله پس از ثبت انجام می‌شود.
        manage dispatch_events
        ;;
    finance)
        # تمدید/انقضای قراردادها و کنترل مغایرت مالی؛ در صورت مغایرت با کد خطا خارج می‌شود تا ایمیل Cron برسد.
        manage finance_tick
        manage finance_reconcile
        ;;
    *)
        echo "usage: $0 backup|nightly|reminders|telemed|events|finance" >&2
        exit 2
        ;;
esac
