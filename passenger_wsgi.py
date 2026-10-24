# -*- coding: utf-8 -*-
"""ورودی برنامه برای Passenger در هاست cPanel (Application Manager).

Passenger این فایل را با پایتون پیش‌فرض سرور اجرا می‌کند؛ اگر محیط پایتون پروژه
(پوشه venv کنار پوشه کد) وجود داشته باشد، برنامه با همان پایتون دوباره اجرا می‌شود.
این بخش عمداً با نسخه‌های قدیمی پایتون هم سازگار نوشته شده است.
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.environ.get("APP_VENV", os.path.join(os.path.dirname(APP_DIR), "venv"))
VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")

if os.path.exists(VENV_PYTHON) and os.path.realpath(sys.prefix) != os.path.realpath(VENV_DIR):
    os.execl(VENV_PYTHON, VENV_PYTHON, *sys.argv)

sys.path.insert(0, APP_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
