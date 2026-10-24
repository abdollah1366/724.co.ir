#!/usr/bin/env bash
# به‌روزرسانی سامانه ۷۲۴ به آخرین نسخه شاخه (پیش‌فرض main)، با پشتیبان پیش از تغییر.
#
#   sudo bash /srv/724/app/deploy/update.sh
set -euo pipefail
umask 022

BRANCH="${BRANCH:-main}"
APP=/srv/724/app
VENV=/srv/724/venv

[ "$(id -u)" -eq 0 ] || { echo "با sudo اجرا کنید." >&2; exit 1; }
manage() { /usr/local/bin/724-manage "$@"; }

echo "==> پشتیبان پیش از به‌روزرسانی"
manage backup_db

echo "==> دریافت کد"
before=$(git -C "$APP" rev-parse HEAD)
git -C "$APP" fetch -q origin "$BRANCH"
git -C "$APP" checkout -q -B "$BRANCH" "origin/$BRANCH"
echo "$(git -C "$APP" rev-parse --short "$before") -> $(git -C "$APP" rev-parse --short HEAD)"

echo "==> بسته‌های پایتون و پایگاه داده"
"$VENV/bin/pip" install -q -r "$APP/requirements-prod.txt"
manage migrate --noinput
manage createcachetable
manage collectstatic --noinput -v 0
manage check --deploy --fail-level ERROR

echo "==> راه‌اندازی دوباره"
install -m 644 "$APP"/deploy/systemd/724-* /etc/systemd/system/
systemctl daemon-reload
systemctl restart 724-web.service
sleep 2
systemctl is-active --quiet 724-web.service || { echo "سرویس بالا نیامد: journalctl -u 724-web -n 50" >&2; exit 1; }
echo "به‌روزرسانی انجام شد. نسخه قبلی: $before (بازگشت: docs/DEPLOY.md)"
