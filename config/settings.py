"""
تنظیمات پروژه ۷۲۴.

مقادیر حساس (کلید مخفی، تنظیمات پایگاه داده، حالت DEBUG) از متغیرهای محیطی
خوانده می‌شوند تا هیچ رازی داخل مخزن ذخیره نشود. فایل .env.example را ببینید.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path):
    """خواندن ساده فایل .env (بدون وابستگی اضافه). متغیرهای از پیش تنظیم‌شده بازنویسی نمی‌شوند."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        # فقط برای توسعه محلی؛ در محیط واقعی حتماً DJANGO_SECRET_KEY را تنظیم کنید.
        SECRET_KEY = "dev-only-insecure-key-change-me"
    else:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG is off.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.clinic",
    "apps.appointments",
    "apps.shop",
    "apps.accounts",
    "apps.accounting",
    "apps.inventory",
    "apps.purchasing",
    "apps.sales",
    "apps.backoffice",
    "apps.audit",  # باید آخر باشد تا نقش‌ها پس از ساخت همه مجوزها تنظیم شوند
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.audit.context.AuditContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site_info",
                "apps.shop.context_processors.cart",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# پایگاه داده: پیش‌فرض SQLite برای توسعه؛ در محیط واقعی PostgreSQL.
if os.environ.get("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ.get("POSTGRES_USER", ""),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fa"
LANGUAGES = [("fa", "فارسی")]
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# امنیت
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# اطلاعات عمومی سایت (در قالب‌ها استفاده می‌شود)
SITE_NAME = "۷۲۴"
SITE_TAGLINE = "همراه سلامت شما، در تمام روزهای هفته"
SITE_PHONE = os.environ.get("SITE_PHONE", "")
SITE_ADDRESS = os.environ.get("SITE_ADDRESS", "")

# نوبت‌دهی
APPOINTMENT_BOOKING_DAYS_AHEAD = 30

# ورود کاربران
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "core:home"

# قوانین باشگاه مشتریان (تعداد ژتون). مقادیر را مدیریت مجموعه تعیین می‌کند.
CLUB_RULES = {
    "appointment_done": 50,  # هر ویزیت یا خدمت انجام‌شده
    "order_rial_per_point": 100_000,  # هر ۱۰ هزار تومان خرید = ۱ ژتون
    "referral_referrer": 100,  # به معرف، وقتی دوستش اولین خدمت را گرفت
    "referral_new_member": 50,  # به عضو جدید با کد معرف، پس از اولین خدمت
}

# سطح‌های عضویت بر اساس مجموع ژتون‌های به‌دست‌آمده: (نام، حداقل ژتون)
CLUB_LEVELS = [("برنزی", 0), ("نقره‌ای", 500), ("طلایی", 1500)]

# قواعد داروخانه و انبار
PHARMACY_RULES = {
    "min_shelf_life_days_on_receipt": int(os.environ.get("MIN_SHELF_LIFE_DAYS", "90")),  # کمترین عمر باقی‌مانده هنگام خرید
    "expiry_alert_days": 90,  # هشدار نزدیک به انقضا
    "quarantine_warehouse_code": "QRN",
    "online_warehouse_code": os.environ.get("ONLINE_WAREHOUSE_CODE", ""),  # خالی = اولین انبار فروش
}

# پشتیبان‌گیری: python manage.py backup_db
BACKUP_DIR = os.environ.get("BACKUP_DIR", str(BASE_DIR / "backups"))

LOG_DIR = os.environ.get("LOG_DIR", "")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
if LOG_DIR:
    LOGGING["handlers"]["file"] = {
        "class": "logging.handlers.RotatingFileHandler", "filename": os.path.join(LOG_DIR, "724.log"),
        "maxBytes": 10 * 1024 * 1024, "backupCount": 10, "encoding": "utf-8", "formatter": "plain",
    }
    LOGGING["root"]["handlers"].append("file")
