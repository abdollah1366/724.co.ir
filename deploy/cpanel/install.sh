#!/usr/bin/env bash
# نصب سامانه ۷۲۴ روی هاست cPanel (Passenger + MySQL/MariaDB)، بدون نیاز به root.
# در Terminal خود cPanel اجرا کنید:
#
#   DOMAIN=724med.ir bash install.sh
#
# پایگاه داده، کاربر آن و ثبت برنامه در Application Manager با API خود cPanel انجام می‌شود.
# اجرای دوباره امن است: رمزها و داده‌ها حفظ می‌شوند. راهنما: docs/HOSTING.md
set -euo pipefail
umask 027

DOMAIN="${DOMAIN:-724med.ir}"
REPO_URL="${REPO_URL:-https://github.com/abdollah1366/724.co.ir.git}"
BRANCH="${BRANCH:-main}"
PYTHON="${PYTHON:-}"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"

BASE=$HOME/724
APP=$BASE/app
VENV=$BASE/venv
ENV_FILE=$APP/.env

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mخطا: %s\033[0m\n' "$*" >&2; exit 1; }
manage() { (cd "$APP" && "$VENV/bin/python" manage.py "$@"); }
set_env() {
    local key=$1 value=$2
    if grep -q "^${key}=" "$ENV_FILE"; then
        local escaped
        escaped=$(printf '%s' "$value" | sed 's/[&|\\]/\\&/g')
        sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
    fi
}
get_env() { grep -m1 "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true; }
random_hex() { "$PYTHON" -c "import secrets; print(secrets.token_hex($1))"; }
uapi_ok() { uapi --output=json "$@" 2>/dev/null | grep -q '"status":1'; }

# سرورهای داخل ایران اغلب به pypi.org دسترسی ندارند یا کند هستند؛ مخزن‌های داخلی به‌ترتیب امتحان می‌شوند
# و اولین مخزنی که کار کرد در ~/724/.pip-index ذخیره می‌شود (update.sh هم از آن استفاده می‌کند).
PIP_INDEXES="${PIP_INDEX_URL:-} https://pypi.org/simple https://mirror-pypi.runflare.com/simple \
https://package-mirror.liara.ir/repository/pypi/simple https://repo.hmirror.ir/python/simple"
pip_install() {
    local pip=$1 index saved
    shift
    saved=$(cat "$BASE/.pip-index" 2>/dev/null || true)
    for index in $saved $PIP_INDEXES; do
        echo "  دریافت بسته‌ها از $index"
        if "$pip" install -q --progress-bar off --retries 1 --timeout 30 --index-url "$index" "$@"; then
            mkdir -p "$BASE"
            echo "$index" >"$BASE/.pip-index"
            return 0
        fi
        warn "از $index دریافت نشد؛ مخزن بعدی امتحان می‌شود."
    done
    return 1
}

[ "$(id -u)" -ne 0 ] || die "این اسکریپت برای کاربر هاست است، نه root. برای سرور اختصاصی deploy/install.sh را ببینید."
[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "DOMAIN نامعتبر است: $DOMAIN"

step "پیدا کردن پایتون 3.10 یا جدیدتر"
if [ -z "$PYTHON" ]; then
    best=""
    for candidate in /opt/alt/python3*/bin/python3.* /opt/cpanel/ea-python3*/root/usr/bin/python3 \
        /usr/local/bin/python3.* /usr/bin/python3.* "$(command -v python3 || true)"; do
        [ -x "$candidate" ] || continue
        case "$candidate" in *-config | *m) continue ;; esac
        version=$("$candidate" -c 'import sys; print("%d%02d" % sys.version_info[:2])' 2>/dev/null) || continue
        if [ "$version" -ge "${PYTHON_MIN:-310}" ] && { [ -z "$best" ] || [ "$version" -gt "${best%%:*}" ]; }; then
            best="$version:$candidate"
        fi
    done
    if [ -n "$best" ]; then
        PYTHON=${best#*:}
    else
        # پایتون سرور قدیمی است: یک پایتون مستقل در پوشه حساب نصب می‌شود (بدون root و بدون کامپایل).
        echo "پایتون سرور قدیمی است؛ نصب پایتون 3.12 در $BASE/python ..."
        export UV_PYTHON_INSTALL_DIR="$BASE/python"
        UV="$BASE/tools/bin/uv"
        if [ ! -x "$UV" ]; then
            # ابزار uv از PyPI (با پایتون فعلی سرور) و اگر نشد از سایت سازنده.
            { python3 -m venv "$BASE/tools" && pip_install "$BASE/tools/bin/pip" uv; } ||
                { mkdir -p "$BASE/tools/bin" && curl -LsSf https://astral.sh/uv/install.sh |
                    env UV_INSTALL_DIR="$BASE/tools/bin" INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null; } ||
                die "دریافت ابزار نصب پایتون (uv) ناموفق بود."
        fi
        "$UV" python install -q 3.12 || die "نصب پایتون 3.12 ناموفق بود."
        PYTHON=$("$UV" python find 3.12)
    fi
fi
echo "$PYTHON ($("$PYTHON" --version 2>&1))"

step "دریافت کد (شاخه $BRANCH)"
mkdir -p "$BASE"
chmod 711 "$BASE"
if [ -d "$APP/.git" ]; then
    git -C "$APP" fetch -q origin "$BRANCH"
    git -C "$APP" checkout -q -B "$BRANCH" "origin/$BRANCH"
else
    git clone -q --branch "$BRANCH" "$REPO_URL" "$APP"
fi
mkdir -p "$BASE/media" "$BASE/static" "$BASE/private_media" "$BASE/backups" "$BASE/logs" "$APP/tmp"
chmod 700 "$BASE/private_media" "$BASE/backups" "$BASE/logs"
chmod +x "$APP/deploy/"*.sh "$APP/deploy/cpanel/"*.sh

step "محیط پایتون و بسته‌ها"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
pip_install "$VENV/bin/pip" --upgrade pip || warn "به‌روزرسانی pip ممکن نشد؛ ادامه با نسخه فعلی."
pip_install "$VENV/bin/pip" -r "$APP/requirements-hosting.txt" ||
    die "دریافت بسته‌های پایتون از هیچ مخزنی ممکن نشد. آدرس مخزن PyPI را از پشتیبانی هاست بپرسید و اسکریپت را با PIP_INDEX_URL=آدرس دوباره اجرا کنید."

step "پایگاه داده MySQL"
[ -f "$ENV_FILE" ] || install -m 600 /dev/null "$ENV_FILE"
if [ -z "$(get_env MYSQL_DATABASE)" ]; then
    db_name="${USER}_724"
    db_user="${USER}_724"
    db_password="$(random_hex 16)Aa1!"
    if command -v uapi >/dev/null &&
        uapi_ok Mysql create_database name="$db_name" &&
        uapi_ok Mysql create_user name="$db_user" password="$db_password" &&
        uapi_ok Mysql set_privileges_on_database user="$db_user" database="$db_name" privileges="ALL PRIVILEGES"; then
        echo "پایگاه داده $db_name ساخته شد."
    else
        warn "ساخت خودکار پایگاه داده ممکن نشد. در cPanel > MySQL Database Wizard یک پایگاه داده و کاربر با همه دسترسی‌ها بسازید و این‌جا وارد کنید."
        read -rp "نام پایگاه داده: " db_name
        read -rp "نام کاربر پایگاه داده: " db_user
        read -rsp "رمز کاربر پایگاه داده (نمایش داده نمی‌شود): " db_password
        echo
    fi
    set_env MYSQL_DATABASE "$db_name"
    set_env MYSQL_USER "$db_user"
    set_env MYSQL_PASSWORD "$db_password"
fi
if [ -z "$(get_env MYSQL_HOST)" ]; then
    # اتصال از راه سوکت محلی، اگر وجود دارد (مثل phpMyAdmin)
    [ -S /var/lib/mysql/mysql.sock ] && set_env MYSQL_HOST /var/lib/mysql/mysql.sock || set_env MYSQL_HOST localhost
fi

step "تنظیمات (.env)"
[ -n "$(get_env DJANGO_SECRET_KEY)" ] || set_env DJANGO_SECRET_KEY "$(random_hex 32)"
[ -n "$(get_env DJANGO_ADMIN_URL)" ] || set_env DJANGO_ADMIN_URL "manage-$(random_hex 5)/"
[ -n "$(get_env DJANGO_HSTS_SECONDS)" ] || set_env DJANGO_HSTS_SECONDS 3600
[ -n "$(get_env SMS_BACKEND)" ] || set_env SMS_BACKEND console
set_env DJANGO_DEBUG false
set_env DJANGO_ALLOWED_HOSTS "$DOMAIN,www.$DOMAIN"
set_env DJANGO_CSRF_TRUSTED_ORIGINS "https://$DOMAIN,https://www.$DOMAIN"
set_env DJANGO_SERVE_STATIC true
set_env DJANGO_SERVE_MEDIA true
set_env DJANGO_CACHE_BACKEND db
set_env SITE_URL "https://$DOMAIN"
set_env STATIC_ROOT "$BASE/static"
set_env MEDIA_ROOT "$BASE/media"
set_env PRIVATE_MEDIA_ROOT "$BASE/private_media"
set_env BACKUP_DIR "$BASE/backups"
set_env LOG_DIR "$BASE/logs"
chmod 600 "$ENV_FILE"

step "ساخت جدول‌ها و فایل‌های ثابت"
# فارسی و ایموجی به utf8mb4 نیاز دارد؛ پایگاه داده تازه ممکن است latin1 باشد.
manage shell -c "
from django.db import connection
with connection.cursor() as c:
    c.execute('ALTER DATABASE \`%s\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci' % connection.settings_dict['NAME'])
"
manage migrate --noinput
manage createcachetable
manage collectstatic --noinput -v 0
manage seed_services
manage seed_lab
manage seed_ehr
manage seed_blog
manage check --deploy --fail-level ERROR

step "ثبت برنامه در Application Manager"
if command -v uapi >/dev/null && uapi --output=json PassengerApps list_applications 2>/dev/null | grep -q '"name":"724"'; then
    echo "برنامه از قبل ثبت شده است."
elif command -v uapi >/dev/null && uapi_ok PassengerApps register_application name=724 path="724/app" \
    domain="$DOMAIN" base_uri=/ deployment_mode=production enabled=1; then
    echo "برنامه 724 برای $DOMAIN ثبت شد."
else
    warn "ثبت خودکار ممکن نشد. در cPanel > Application Manager یک برنامه با این مشخصات بسازید:
    Application Name: 724   Deployment Domain: $DOMAIN   Base Application URL: /
    Application Path: 724/app   Deployment Environment: Production"
fi
touch "$APP/tmp/restart.txt"

step "بررسی"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "https://$DOMAIN/" || true)
if [ "$code" = 200 ]; then
    echo "سایت پاسخ داد (200)."
else
    warn "https://$DOMAIN/ کد $code برگرداند. اگر 000 است، گواهی SSL هنوز صادر نشده (cPanel > SSL/TLS Status > Run AutoSSL). لاگ: ~/724/logs/724.log"
fi

cat <<EOF

نصب تمام شد.
  آدرس سایت:   https://$DOMAIN
  پنل مدیریت:  https://$DOMAIN/$(get_env DJANGO_ADMIN_URL)

قدم بعدی:
  ۱. ساخت حساب مدیر ارشد:
       cd ~/724/app && ~/724/venv/bin/python manage.py createsuperuser
  ۲. در cPanel > Cron Jobs این سه خط را اضافه کنید (راهنما: docs/HOSTING.md):
       30 2 * * *   BACKUP_KEEP_DAYS=14 bash $APP/deploy/tasks.sh backup
       30 0 * * *   bash $APP/deploy/tasks.sh nightly
       0 10 * * *   bash $APP/deploy/tasks.sh reminders
EOF
