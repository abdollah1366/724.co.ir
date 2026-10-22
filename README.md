# ۷۲۴ | سامانه جامع سلامت ساختمان ۷۲۴

وب‌سایت و سامانه نوبت‌دهی ساختمان سلامت ۷۲۴ (724.co.ir)، ساخته‌شده با Django و کاملاً فارسی و راست‌چین.

> وضعیت: **مرحله اول** (معرفی ساختمان و طبقات، پزشکان، نوبت‌دهی اینترنتی، فرم تماس، پنل مدیریت).
> معماری و نقشه راه مراحل بعد در [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) آمده است.

## اجرا روی کامپیوتر خودتان (VS Code)

پیش‌نیاز: Python نسخه 3.12 یا بالاتر، Git و VS Code.

۱. دریافت پروژه (در ترمینال VS Code با `` Ctrl+` ``):

```bash
git clone https://github.com/abdollah1366/724.co.ir.git
cd 724.co.ir
code .
```

۲. ساخت محیط مجازی و نصب وابستگی‌ها:

```bash
# ویندوز (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
# اگر خطای اجرای اسکریپت گرفتید یک بار این را بزنید:
# Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# لینوکس / مک
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

۳. تنظیمات محلی: فایل `.env.example` را با نام `.env` کپی کنید (ویندوز: `copy .env.example .env`، لینوکس/مک: `cp .env.example .env`). مقدار `DJANGO_DEBUG=true` برای توسعه کافی است.

۴. ساخت پایگاه داده، اطلاعات پایه و کاربر مدیر:

```bash
python manage.py migrate
python manage.py seed_floors --demo-doctors   # طبقات ۶گانه + دو پزشک نمونه برای آزمایش
python manage.py createsuperuser
```

۵. اجرای سایت:

```bash
python manage.py runserver
```

سپس در مرورگر باز کنید:
- سایت: http://127.0.0.1:8000
- پنل مدیریت: http://127.0.0.1:8000/admin

در VS Code می‌توانید از منوی **Run and Debug** (کلید `F5`) گزینه «اجرای سایت» یا «اجرای تست‌ها» را هم انتخاب کنید. هنگام باز کردن پروژه، مفسر Python را روی `.venv` بگذارید (`Ctrl+Shift+P` ← *Python: Select Interpreter*).

## اجرای تست‌ها

```bash
python manage.py test
```

هر Pull Request به‌صورت خودکار با GitHub Actions تست می‌شود (`.github/workflows/tests.yml`).

## ساختار پروژه

```
config/            تنظیمات و مسیرهای اصلی Django
apps/core/         صفحات عمومی (خانه، درباره، تماس، حریم خصوصی)، اعتبارسنج‌ها و فیلترهای فارسی
apps/clinic/       طبقات، خدمات، تخصص‌ها، پزشکان و برنامه حضور
apps/appointments/ نوبت‌دهی، پیگیری و لغو نوبت
templates/         قالب‌های HTML راست‌چین
static/css/        استایل سایت
docs/              مستندات معماری و نقشه راه
```

## کار با پنل مدیریت

- **طبقات و خدمات**: عنوان، توضیحات، ساعات کاری و تعرفه هر خدمت (به ریال).
- **پزشکان**: شماره نظام پزشکی الزامی است. برنامه حضور هفتگی داخل صفحه هر پزشک تعریف می‌شود و زمان‌های نوبت خودکار ساخته می‌شوند.
- **نوبت‌ها**: مشاهده، جست‌وجو، تأیید یا لغو گروهی.
- **پیام‌های تماس**: پیام‌های رسیده از فرم تماس.

پیش از انتشار واقعی، پزشکان نمونه (`demo-gp` و `demo-cardio`) را از پنل حذف کنید.

## نکات امنیتی

- فایل `.env` و `db.sqlite3` هرگز نباید وارد گیت شوند (در `.gitignore` هستند).
- در سرور واقعی: `DJANGO_DEBUG=false`، یک `DJANGO_SECRET_KEY` طولانی و تصادفی، `DJANGO_ALLOWED_HOSTS=724.co.ir,www.724.co.ir`، پایگاه داده PostgreSQL و HTTPS الزامی است. آدرس پنل مدیریت را هم با `DJANGO_ADMIN_URL` تغییر دهید.
