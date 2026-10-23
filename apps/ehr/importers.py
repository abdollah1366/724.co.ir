"""ورود کاتالوگ‌های رسمی از فایل اکسل یا CSV. ورود دوباره همان فایل ردیف‌ها را به‌روز می‌کند و چیزی حذف نمی‌شود."""

import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction

from .models import GenericDrug, ICD10Code, ServiceCode

MODIFIER_SHEET = "تعدیلی"


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _decimal(value):
    text = _text(value)
    if not text or text in ("-", "—"):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _rows(path):
    """(نام برگه، ردیف‌ها) برای xlsx یا CSV."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            yield path.stem, [tuple(row) for row in csv.reader(handle)]
        return
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            yield sheet.title.strip(), list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()


def _find_columns(header, rules):
    """ستون هر فیلد را با کلمات کلیدی سرستون پیدا می‌کند."""
    columns = {}
    for index, cell in enumerate(header):
        label = _text(cell)
        for name, test in rules.items():
            if name not in columns and label and test(label):
                columns[name] = index
    return columns


SERVICE_RULES = {
    "attribute": lambda h: "ویژگی" in h,
    "code": lambda h: ("کد" in h or "code" in h.lower()) and "ویژگی" not in h and "شرح" not in h and "تعدیلی" not in h,
    "title": lambda h: "شرح" in h or "عنوان" in h,
    "description": lambda h: "توضیح" in h,
    "professional": lambda h: "حرفه" in h,
    "technical": lambda h: h == "فنی",
    "anesthesia": lambda h: "بیهوشی" in h,
    "total": lambda h: h in ("کل", "ارزش نسبی"),
}


def import_service_codes(path, edition="1404"):
    stats = {"created": 0, "updated": 0, "sheets": {}}
    with transaction.atomic():
        for sheet, rows in _rows(path):
            if MODIFIER_SHEET in sheet:
                continue
            header_index = next(
                (i for i, row in enumerate(rows[:10]) if any("شرح" in _text(c) or "عنوان" in _text(c) for c in row)), None
            )
            if header_index is None:
                continue
            columns = _find_columns(rows[header_index], SERVICE_RULES)
            if "code" not in columns or "title" not in columns:
                continue
            count = 0
            for row in rows[header_index + 1:]:
                get = lambda name: row[columns[name]] if name in columns and columns[name] < len(row) else None  # noqa: E731
                code = _text(get("code"))
                if not re.fullmatch(r"\d{5,7}", code):
                    continue
                defaults = {
                    "title": _text(get("title")), "description": _text(get("description")),
                    "attribute": _text(get("attribute")), "sheet": sheet, "edition": edition,
                    "total_rvu": _decimal(get("total")), "professional_rvu": _decimal(get("professional")),
                    "technical_rvu": _decimal(get("technical")), "anesthesia_base": _decimal(get("anesthesia")),
                }
                if not defaults["title"]:
                    continue
                existing = ServiceCode.objects.filter(code=code).first()
                if existing and existing.sheet != sheet and existing.total_rvu is not None and defaults["total_rvu"] is None:
                    continue  # کد تکراری در برگه فهرستی؛ ارزش نسبی برگه اصلی حفظ می‌شود
                _, created = ServiceCode.objects.update_or_create(code=code, defaults=defaults)
                stats["created" if created else "updated"] += 1
                count += 1
            stats["sheets"][sheet] = count
    return stats


GENERIC_RULES = {
    "code": lambda h: "کد" in h and "atc" not in h.lower() or h.lower() in ("code", "generic code"),
    "name": lambda h: h.lower() in ("name", "generic name", "generic") or "نام ژنریک" in h or h == "نام لاتین",
    "name_fa": lambda h: "فارسی" in h,
    "dosage_form": lambda h: "شکل" in h or "form" in h.lower(),
    "strength": lambda h: "قدرت" in h or "strength" in h.lower() or "دوز" in h,
    "route": lambda h: "راه" in h or "route" in h.lower(),
    "atc": lambda h: "atc" in h.lower(),
    "category": lambda h: "دسته" in h or "category" in h.lower() or "otc" in h.lower(),
}


def _category(value):
    value = _text(value).lower()
    if "otc" in value or "بدون نسخه" in value:
        return GenericDrug.Category.OTC
    if "کنترل" in value or "مخدر" in value or "controlled" in value:
        return GenericDrug.Category.CONTROLLED
    return GenericDrug.Category.RX


def import_generics(path, verified=True):
    stats = {"created": 0, "updated": 0}
    with transaction.atomic():
        for _, rows in _rows(path):
            if not rows:
                continue
            header_index = next((i for i, row in enumerate(rows[:10]) if "name" in _find_columns(row, GENERIC_RULES)), None)
            if header_index is None:
                continue
            columns = _find_columns(rows[header_index], GENERIC_RULES)
            for row in rows[header_index + 1:]:
                get = lambda name: row[columns[name]] if name in columns and columns[name] < len(row) else None  # noqa: E731
                name = _text(get("name"))
                if not name:
                    continue
                key = {"name": name, "dosage_form": _text(get("dosage_form")) or "-", "strength": _text(get("strength"))}
                defaults = {
                    "code": _text(get("code")) or None, "name_fa": _text(get("name_fa")), "route": _text(get("route")),
                    "atc_code": _text(get("atc")), "category": _category(get("category")), "needs_verification": not verified,
                }
                if defaults["code"] and GenericDrug.objects.filter(code=defaults["code"]).exclude(**key).exists():
                    defaults.pop("code")
                _, created = GenericDrug.objects.update_or_create(**key, defaults=defaults)
                stats["created" if created else "updated"] += 1
    return stats


def import_icd10(path):
    stats = {"created": 0, "updated": 0}
    with transaction.atomic():
        for _, rows in _rows(path):
            for row in rows:
                code = _text(row[0]).upper() if row else ""
                if not re.fullmatch(r"[A-Z]\d{2}(\.\d{1,2})?", code):
                    continue
                defaults = {"title": _text(row[1]) if len(row) > 1 else ""}
                if len(row) > 2 and _text(row[2]):
                    defaults["title_fa"] = _text(row[2])
                _, created = ICD10Code.objects.update_or_create(code=code, defaults=defaults)
                stats["created" if created else "updated"] += 1
    return stats


def link_products_to_generics():
    """کالاهای داروخانه را با نام ژنریک و قدرت به فهرست ژنریک وصل می‌کند (فقط کالاهای بدون اتصال)."""
    from apps.shop.models import Product, ProductKind

    linked = 0
    for product in Product.objects.filter(kind=ProductKind.DRUG, generic__isnull=True).exclude(generic_name=""):
        candidates = GenericDrug.objects.filter(name__iexact=product.generic_name.strip(), is_active=True)
        if product.strength:
            narrowed = candidates.filter(strength__iexact=product.strength.strip())
            candidates = narrowed if narrowed.exists() else candidates
        if product.dosage_form and candidates.count() > 1:
            narrowed = candidates.filter(dosage_form__icontains=product.dosage_form.strip())
            candidates = narrowed if narrowed.exists() else candidates
        if candidates.count() == 1:
            product.generic = candidates.get()
            product.save(update_fields=["generic"])
            linked += 1
    return linked
