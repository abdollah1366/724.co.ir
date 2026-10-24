#!/usr/bin/env bash
# به‌روزرسانی سامانه ۷۲۴ روی هاست cPanel، با پشتیبان پیش از تغییر.
#
#   bash ~/724/app/deploy/cpanel/update.sh
set -euo pipefail
umask 027

BRANCH="${BRANCH:-main}"
APP=$HOME/724/app
VENV=$HOME/724/venv
manage() { (cd "$APP" && "$VENV/bin/python" manage.py "$@"); }

echo "==> پشتیبان پیش از به‌روزرسانی"
manage backup_db --keep-days "${BACKUP_KEEP_DAYS:-14}"

echo "==> دریافت کد"
before=$(git -C "$APP" rev-parse HEAD)
git -C "$APP" fetch -q origin "$BRANCH"
git -C "$APP" checkout -q -B "$BRANCH" "origin/$BRANCH"
echo "$(git -C "$APP" rev-parse --short "$before") -> $(git -C "$APP" rev-parse --short HEAD)"

echo "==> بسته‌های پایتون و پایگاه داده"
index=$(cat "$HOME/724/.pip-index" 2>/dev/null || echo https://pypi.org/simple)
"$VENV/bin/pip" install -q --progress-bar off --timeout 30 --index-url "$index" -r "$APP/requirements-hosting.txt"
manage migrate --noinput
manage createcachetable
manage collectstatic --noinput -v 0
manage check --deploy --fail-level ERROR

mkdir -p "$APP/tmp"
touch "$APP/tmp/restart.txt"
echo "به‌روزرسانی انجام شد. نسخه قبلی: $before (بازگشت: docs/HOSTING.md)"
