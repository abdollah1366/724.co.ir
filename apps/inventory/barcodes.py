"""بارکد در انبار و صندوق: خواندن کد دوبعدی GS1 (DataMatrix بسته دارو)، بارکد داخلی کالا و برچسب بچ.

بارکدخوان (USB یا بلوتوث) مثل صفحه‌کلید متن را تایپ می‌کند و Enter می‌زند؛ دوربین موبایل همان متن را می‌خواند.
همه ورودی‌ها از resolve() می‌گذرند تا کالا، ضریب بسته‌بندی، بچ، انقضا و سریال پیدا شود.

قالب‌های پشتیبانی‌شده:
- بارکد معمولی کالا (EAN-13 / GTIN) که در «بسته‌بندی و بارکد» ثبت شده؛ GTIN-14 با صفرهای ابتدایی هم پیدا می‌شود.
- کد GS1 با شناسه‌های کاربردی (01) GTIN، (17) انقضا، (10) بچ، (21) سریال؛ با جداکننده FNC1 یا به شکل پرانتزدار.
- برچسب داخلی بچ «BT» + شماره (چاپ از همین سامانه).
- کد کالا (SKU) یا نامک.
"""

import calendar
import datetime
import re
from dataclasses import dataclass

from apps.core.validators import normalize_digits
from apps.shop.models import Product, ProductPackage, Unit

from .models import Batch

GS = "\x1d"
INTERNAL_PREFIX = "200"  # بازه داخلی فروشگاهی GS1 (۲۰ تا ۲۹): با بارکد واقعی تولیدکننده تداخل ندارد.
BATCH_PREFIX = "BT"
# شناسه‌های کاربردی پرکاربرد: طول ثابت یا None برای طول متغیر (تا FNC1 یا پایان)
AI_LENGTHS = {"00": 18, "01": 14, "02": 14, "10": None, "11": 6, "13": 6, "15": 6, "17": 6, "21": None, "240": None,
              "30": None, "37": None, "710": None, "711": None, "712": None, "713": None, "714": None}
AI_NAMES = {"01": "gtin", "02": "gtin", "10": "batch", "17": "expiry", "11": "manufactured", "21": "serial"}


@dataclass
class Scan:
    raw: str
    product: Product = None
    factor: int = 1
    batch: Batch = None
    batch_number: str = ""
    expiry: datetime.date = None
    serial: str = ""
    gtin: str = ""
    source: str = ""  # package / gs1 / batch_label / sku

    @property
    def found(self):
        return self.product is not None

    def as_dict(self):
        p = self.product
        return {
            "raw": self.raw, "source": self.source, "gtin": self.gtin, "factor": self.factor,
            "batch": self.batch.number if self.batch else self.batch_number,
            "expiry": self.expiry.isoformat() if self.expiry else None, "serial": self.serial,
            "product": {"id": p.pk, "name": p.name, "sku": p.sku, "price": p.price, "stock": p.stock,
                        "requires_prescription": p.requires_prescription, "is_controlled": p.is_controlled,
                        "storage_condition": p.get_storage_condition_display()} if p else None,
        }


def _gs1_date(value):
    if not re.fullmatch(r"\d{6}", value or ""):
        return None
    year, month, day = 2000 + int(value[:2]), int(value[2:4]), int(value[4:])
    if not 1 <= month <= 12:
        return None
    if day == 0:  # روز ۰۰ یعنی پایان همان ماه
        day = calendar.monthrange(year, month)[1]
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def parse_gs1(text):
    """متن GS1 → {"gtin", "batch", "expiry", "serial", ...}. اگر متن GS1 نباشد {} برمی‌گرداند."""
    text = (text or "").strip()
    for prefix in ("]d2", "]C1", "]Q3", "]e0"):  # شناسه نوع نماد که برخی بارکدخوان‌ها اول متن می‌فرستند
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.replace("<GS>", GS).replace("{GS}", GS).replace("\\x1d", GS)
    fields = {}
    if text.startswith("("):
        for ai, value in re.findall(r"\((\d{2,4})\)([^(]*)", text):
            fields[ai] = value.strip()
    else:
        i = 0
        if not text[:2].isdigit():
            return {}
        while i < len(text):
            if text[i] == GS:
                i += 1
                continue
            ai = next((a for a in sorted(AI_LENGTHS, key=len, reverse=True) if text.startswith(a, i)), None)
            if ai is None:
                return {} if not fields else _named(fields)
            i += len(ai)
            length = AI_LENGTHS[ai]
            if length:
                value = text[i:i + length]
                i += length
            else:
                end = text.find(GS, i)
                end = len(text) if end == -1 else end
                value = text[i:end]
                i = end
            fields[ai] = value
    if "01" not in fields and "02" not in fields:
        return {}
    return _named(fields)


def _named(fields):
    result = {}
    for ai, value in fields.items():
        name = AI_NAMES.get(ai)
        if name in ("expiry", "manufactured"):
            result[name] = _gs1_date(value)
        elif name:
            result[name] = value
    return result


def gtin_variants(code):
    """یک GTIN را به همه شکل‌های رایج (۸، ۱۲، ۱۳، ۱۴ رقمی با صفر ابتدایی) تبدیل می‌کند."""
    digits = code.lstrip("0")
    variants = {code}
    for length in (8, 12, 13, 14):
        if len(digits) <= length:
            variants.add(digits.zfill(length))
    return variants


def ean13_check_digit(twelve):
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(twelve))
    return str((10 - total % 10) % 10)


def is_valid_ean13(code):
    return bool(re.fullmatch(r"\d{13}", code)) and ean13_check_digit(code[:12]) == code[12]


def internal_barcode(product):
    body = f"{INTERNAL_PREFIX}{product.pk:09d}"
    return body + ean13_check_digit(body)


def ensure_barcode(product):
    """بارکد اصلی کالا؛ اگر بارکدی ثبت نشده، بارکد داخلی EAN-13 می‌سازد و روی بسته‌بندی واحد پایه ثبت می‌کند."""
    package = product.packages.exclude(barcode__isnull=True).exclude(barcode="").order_by("factor", "pk").first()
    if package:
        return package.barcode
    unit = product.base_unit or Unit.objects.get_or_create(name="عدد")[0]
    code = internal_barcode(product)
    package = product.packages.filter(unit=unit).first()
    if package:
        ProductPackage.objects.filter(pk=package.pk).update(barcode=code)
    else:
        ProductPackage.objects.create(product=product, unit=unit, factor=1, barcode=code)
    return code


def batch_label(batch):
    return f"{BATCH_PREFIX}{batch.pk:08d}"


def _by_package(code):
    package = ProductPackage.objects.select_related("product").filter(barcode__in=gtin_variants(code)).first()
    return (package.product, package.factor) if package else (None, 1)


def resolve(raw):
    """متن اسکن‌شده → Scan. هرگز خطا نمی‌دهد؛ اگر کالا پیدا نشود Scan.found نادرست است."""
    text = normalize_digits((raw or "").strip())
    scan = Scan(raw=text)
    if not text:
        return scan
    batch_match = re.fullmatch(rf"{BATCH_PREFIX}(\d{{1,10}})", text.upper())
    if batch_match:
        batch = Batch.objects.select_related("product").filter(pk=int(batch_match.group(1))).first()
        if batch:
            scan.product, scan.batch, scan.batch_number, scan.expiry = batch.product, batch, batch.number, batch.expiry_date
            scan.source = "batch_label"
        return scan
    gs1 = parse_gs1(text) if (len(text) >= 16 or text.startswith(("(", "]")) or GS in text) else {}
    if gs1.get("gtin"):
        scan.gtin, scan.batch_number = gs1["gtin"], gs1.get("batch", "")
        scan.expiry, scan.serial = gs1.get("expiry"), gs1.get("serial", "")
        scan.product, scan.factor = _by_package(scan.gtin)
        scan.source = "gs1"
        if scan.product and scan.batch_number:
            scan.batch = Batch.objects.filter(product=scan.product, number=scan.batch_number).first()
        if scan.product and scan.product.track_serial and scan.serial and not scan.batch_number:
            scan.batch_number = scan.serial
            scan.batch = Batch.objects.filter(product=scan.product, number=scan.serial).first()
        return scan
    if text.isdigit():
        scan.product, scan.factor = _by_package(text)
        if scan.product:
            scan.gtin, scan.source = text, "package"
            return scan
    product = Product.objects.filter(sku=text).first() or Product.objects.filter(slug=text).first()
    if product is None:
        product = Product.objects.filter(name=text).first()
    if product:
        scan.product, scan.source = product, "sku"
    return scan
