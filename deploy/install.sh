#!/usr/bin/env bash
# نصب سامانه ۷۲۴ روی سرور لینوکس (Ubuntu 22.04/24.04 یا Debian 12).
#
#   sudo DOMAIN=724.co.ir EMAIL=you@example.com bash install.sh
#
# بدون DOMAIN سایت با گواهی خودامضا روی IP سرور بالا می‌آید (مرورگر هشدار می‌دهد).
# اجرای دوباره امن است: رمزها و داده‌ها حفظ می‌شوند و فقط تنظیمات به‌روز می‌شوند.
# راهنمای کامل: docs/DEPLOY.md
set -euo pipefail
umask 022

DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
REPO_URL="${REPO_URL:-https://github.com/abdollah1366/724.co.ir.git}"
BRANCH="${BRANCH:-main}"
PUBLIC_IP="${PUBLIC_IP:-}"
SKIP_FIREWALL="${SKIP_FIREWALL:-no}"

APP_USER=app724
ROOT=/srv/724
APP=$ROOT/app
VENV=$ROOT/venv
ENV_FILE=$APP/.env
LOG_DIR=/var/log/724
BACKUP_DIR=/var/backups/724
ACME_ROOT=/var/www/letsencrypt
SELF_CERT=/etc/ssl/724/selfsigned.crt
SELF_KEY=/etc/ssl/724/selfsigned.key

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mخطا: %s\033[0m\n' "$*" >&2; exit 1; }
random_hex() { openssl rand -hex "$1"; }
as_app() { runuser -u "$APP_USER" -- "$@"; }
manage() { (cd "$APP" && as_app "$VENV/bin/python" manage.py "$@"); }
psql_admin() { (cd / && runuser -u postgres -- psql -v ON_ERROR_STOP=1 -tAq "$@"); }

# مقدار یک کلید در .env را تنظیم می‌کند (اگر نبود اضافه می‌کند).
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

[ "$(id -u)" -eq 0 ] || die "با sudo یا کاربر root اجرا کنید."
. /etc/os-release
case "${ID:-}" in
    ubuntu | debian) ;;
    *) die "این اسکریپت برای Ubuntu و Debian نوشته شده است (سیستم شما: ${PRETTY_NAME:-نامشخص})." ;;
esac
if [ -n "$DOMAIN" ]; then
    DOMAIN=${DOMAIN#http*://}
    DOMAIN=${DOMAIN%%/*}
    [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "DOMAIN نامعتبر است: $DOMAIN"
fi

step "نصب بسته‌های سیستم"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3 python3-venv python3-dev postgresql nginx certbot \
    ufw fail2ban openssl curl ca-certificates unattended-upgrades
systemctl enable --now postgresql nginx fail2ban >/dev/null
# نصب خودکار به‌روزرسانی‌های امنیتی
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

step "کاربر و پوشه‌ها"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$ROOT" --shell /usr/sbin/nologin "$APP_USER"
# Nginx باید به سوکت برنامه و فایل‌های عمومی دسترسی داشته باشد، نه به مدارک بیماران.
usermod -aG "$APP_USER" www-data
install -d -m 755 -o root -g root "$ROOT"
install -d -m 2750 -o "$APP_USER" -g "$APP_USER" "$ROOT/media" "$ROOT/static"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$ROOT/private_media" "$BACKUP_DIR"
install -d -m 750 -o "$APP_USER" -g "$APP_USER" "$LOG_DIR"
install -d -m 755 "$ACME_ROOT" /etc/724

step "دریافت کد از $REPO_URL (شاخه $BRANCH)"
if [ -d "$APP/.git" ]; then
    git -C "$APP" fetch -q origin "$BRANCH"
    git -C "$APP" checkout -q -B "$BRANCH" "origin/$BRANCH"
else
    git clone -q --branch "$BRANCH" "$REPO_URL" "$APP"
fi
chmod +x "$APP/deploy/"*.sh

step "محیط پایتون"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP/requirements-prod.txt"

step "پایگاه داده PostgreSQL"
[ -f "$ENV_FILE" ] || install -m 600 -o "$APP_USER" -g root /dev/null "$ENV_FILE"
DB_PASSWORD=$(get_env POSTGRES_PASSWORD)
[ -n "$DB_PASSWORD" ] || DB_PASSWORD=$(random_hex 24)
if [ "$(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname='$APP_USER'")" = "1" ]; then
    psql_admin -c "ALTER ROLE $APP_USER WITH LOGIN PASSWORD '$DB_PASSWORD'"
else
    psql_admin -c "CREATE ROLE $APP_USER WITH LOGIN PASSWORD '$DB_PASSWORD'"
fi
if [ "$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname='$APP_USER'")" != "1" ]; then
    (cd / && runuser -u postgres -- createdb -O "$APP_USER" -E UTF8 -T template0 --locale=C.UTF-8 "$APP_USER")
fi

step "تنظیمات (.env)"
if [ -z "$PUBLIC_IP" ]; then
    PUBLIC_IP=$(curl -4 -s --max-time 5 https://api.ipify.org || true)
    [[ "$PUBLIC_IP" =~ ^[0-9.]+$ ]] || PUBLIC_IP=$(hostname -I | awk '{print $1}')
fi
HOSTS="$PUBLIC_IP"
ORIGINS="https://$PUBLIC_IP"
SITE_URL="https://$PUBLIC_IP"
if [ -n "$DOMAIN" ]; then
    HOSTS="$DOMAIN,www.$DOMAIN,$PUBLIC_IP"
    ORIGINS="https://$DOMAIN,https://www.$DOMAIN,$ORIGINS"
    SITE_URL="https://$DOMAIN"
fi
[ -n "$(get_env DJANGO_SECRET_KEY)" ] || set_env DJANGO_SECRET_KEY "$(random_hex 32)"
# آدرس پنل مدیریت حدس‌زدنی نباشد.
[ -n "$(get_env DJANGO_ADMIN_URL)" ] || set_env DJANGO_ADMIN_URL "manage-$(random_hex 5)/"
[ -n "$(get_env DJANGO_HSTS_SECONDS)" ] || set_env DJANGO_HSTS_SECONDS 3600
[ -n "$(get_env SMS_BACKEND)" ] || set_env SMS_BACKEND console
set_env DJANGO_DEBUG false
set_env DJANGO_ALLOWED_HOSTS "$HOSTS"
set_env DJANGO_CSRF_TRUSTED_ORIGINS "$ORIGINS"
set_env DJANGO_BEHIND_PROXY true
set_env DJANGO_CACHE_BACKEND db
set_env SITE_URL "$SITE_URL"
set_env POSTGRES_DB "$APP_USER"
set_env POSTGRES_USER "$APP_USER"
set_env POSTGRES_PASSWORD "$DB_PASSWORD"
set_env POSTGRES_HOST 127.0.0.1
set_env POSTGRES_PORT 5432
set_env STATIC_ROOT "$ROOT/static"
set_env MEDIA_ROOT "$ROOT/media"
set_env PRIVATE_MEDIA_ROOT "$ROOT/private_media"
set_env BACKUP_DIR "$BACKUP_DIR"
set_env LOG_DIR "$LOG_DIR"
chown "$APP_USER":root "$ENV_FILE"
chmod 600 "$ENV_FILE"

step "ساخت جدول‌ها و فایل‌های ثابت"
manage migrate --noinput
manage createcachetable
manage collectstatic --noinput -v 0
# داده‌های پایه (بدون داده نمونه). موارد موجود تغییر نمی‌کنند.
manage seed_services
manage seed_lab
manage seed_ehr
manage seed_blog
manage check --deploy --fail-level ERROR

step "سرویس‌های systemd"
cat >/usr/local/bin/724-manage <<EOF
#!/bin/sh
# اجرای دستورهای مدیریتی Django با کاربر برنامه، مثلاً: sudo 724-manage createsuperuser
cd $APP && exec runuser -u $APP_USER -- $VENV/bin/python manage.py "\$@"
EOF
chmod 755 /usr/local/bin/724-manage
WORKERS=$(($(nproc) * 2 + 1))
[ "$WORKERS" -le 9 ] || WORKERS=9
echo "WORKERS=$WORKERS" >/etc/724/web.env
install -m 644 "$APP"/deploy/systemd/724-* /etc/systemd/system/
systemctl daemon-reload
systemctl enable 724-web.service 724-backup.timer 724-nightly.timer 724-reminders.timer 724-telemed.timer >/dev/null
systemctl restart 724-web.service
systemctl start 724-backup.timer 724-nightly.timer 724-reminders.timer 724-telemed.timer

step "Nginx و HTTPS"
if [ ! -f "$SELF_CERT" ]; then
    install -d -m 755 /etc/ssl/724
    openssl req -x509 -nodes -newkey rsa:2048 -days 825 -subj "/CN=${DOMAIN:-$PUBLIC_IP}" \
        -keyout "$SELF_KEY" -out "$SELF_CERT" 2>/dev/null
    chmod 600 "$SELF_KEY"
fi
render_nginx() {
    local no_ipv6=()
    # سرورهای بدون IPv6: خط‌های listen [::] حذف می‌شوند.
    [ -s /proc/net/if_inet6 ] || no_ipv6=(-e '/listen \[::\]/d')
    sed -e "s|@SERVER_NAME@|${DOMAIN:+$DOMAIN www.$DOMAIN }_|" -e "s|@SSL_CERT@|$1|" -e "s|@SSL_KEY@|$2|" \
        "${no_ipv6[@]}" "$APP/deploy/nginx/724.conf" >/etc/nginx/sites-available/724
    ln -sf /etc/nginx/sites-available/724 /etc/nginx/sites-enabled/724
    rm -f /etc/nginx/sites-enabled/default
    nginx -t -q
    # restart (نه reload) تا عضویت www-data در گروه برنامه اعمال شود.
    systemctl restart nginx
}
LE_DIR=/etc/letsencrypt/live/$DOMAIN
CERT_OK=no
if [ -n "$DOMAIN" ] && [ -f "$LE_DIR/fullchain.pem" ]; then
    CERT_OK=yes
fi
if [ "$CERT_OK" = yes ]; then
    render_nginx "$LE_DIR/fullchain.pem" "$LE_DIR/privkey.pem"
else
    render_nginx "$SELF_CERT" "$SELF_KEY"
fi
if [ -n "$DOMAIN" ] && [ "$CERT_OK" = no ]; then
    names=(-d "$DOMAIN")
    getent ahosts "www.$DOMAIN" >/dev/null && names+=(-d "www.$DOMAIN")
    email=(--register-unsafely-without-email)
    [ -n "$EMAIL" ] && email=(--email "$EMAIL")
    if certbot certonly --webroot -w "$ACME_ROOT" "${names[@]}" "${email[@]}" --agree-tos -n \
        --cert-name "$DOMAIN" --deploy-hook "systemctl reload nginx"; then
        CERT_OK=yes
        render_nginx "$LE_DIR/fullchain.pem" "$LE_DIR/privkey.pem"
    else
        warn "گواهی Let's Encrypt صادر نشد. بررسی کنید رکورد A دامنه $DOMAIN روی IP $PUBLIC_IP باشد و درگاه 80 باز باشد، سپس اسکریپت را دوباره اجرا کنید. فعلاً گواهی خودامضا فعال است."
    fi
fi

if [ "$SKIP_FIREWALL" != yes ]; then
    step "دیوار آتش (ufw)"
    # درگاه(های) فعلی SSH باز می‌ماند تا اتصال شما قطع نشود.
    ssh_ports=$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' || true)
    # درگاه همین اتصال SSH (اگر sudo آن را نگه داشته باشد)
    ssh_ports="$ssh_ports ${SSH_CONNECTION:+$(echo "$SSH_CONNECTION" | awk '{print $4}')}"
    [ -n "${ssh_ports// /}" ] || ssh_ports=22
    for port in $ssh_ports; do ufw allow "$port/tcp" >/dev/null; done
    ufw allow 80/tcp >/dev/null
    ufw allow 443/tcp >/dev/null
    ufw default deny incoming >/dev/null
    ufw --force enable >/dev/null
    ufw status
fi

step "بررسی نهایی"
sleep 2
systemctl is-active --quiet 724-web.service || die "سرویس 724-web بالا نیامد: journalctl -u 724-web -n 50"
code=$(curl -sk --noproxy "*" -o /dev/null -w '%{http_code}' --resolve "${DOMAIN:-$PUBLIC_IP}:443:127.0.0.1" "https://${DOMAIN:-$PUBLIC_IP}/" || true)
[ "$code" = 200 ] || warn "صفحه اصلی کد $code برگرداند. لاگ: journalctl -u 724-web -n 50"

cat <<EOF

نصب تمام شد.
  آدرس سایت:      $SITE_URL
  پنل مدیریت:     $SITE_URL/$(get_env DJANGO_ADMIN_URL)
  گواهی HTTPS:    $([ "$CERT_OK" = yes ] && echo "Let's Encrypt (تمدید خودکار)" || echo "خودامضا (مرورگر هشدار می‌دهد)")
  پشتیبان شبانه:  $BACKUP_DIR (ساعت ۲:۳۰ بامداد، ۳۰ روز نگهداری)

قدم بعدی: ساخت حساب مدیر ارشد
  sudo 724-manage createsuperuser
EOF
