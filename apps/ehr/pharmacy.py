"""نسخه‌نویسی و داروخانه درمانگاه: هشدارها، داروهای فعلی، موجودی برای پزشک و ساخت فاکتور تحویل برای داروساز."""

import datetime
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Allergy, HistoryEntry, Prescription, PrescriptionItem

DEFAULT_COURSE_DAYS = 30


def _names(generic=None, product=None):
    names = set()
    if generic is not None:
        names |= {generic.name.lower(), generic.name_fa.lower()}
    if product is not None:
        names |= {product.name.lower(), (product.generic_name or "").lower()}
        if product.generic_id:
            names |= {product.generic.name.lower(), product.generic.name_fa.lower()}
    return {n.strip() for n in names if n and n.strip()}


def current_medications(patient, on=None):
    """داروهای نسخه‌های صادرشده که دوره مصرفشان تمام نشده (یا ۳۰ روز اخیر اگر مدت ننوشته‌اند)."""
    on = on or timezone.localdate()
    items = PrescriptionItem.objects.filter(
        prescription__patient=patient, prescription__status=Prescription.Status.ISSUED,
        prescription__issued_at__date__gte=on - datetime.timedelta(days=365),
    ).select_related("generic", "product__generic", "prescription")
    active = []
    for item in items:
        days = item.duration_days or DEFAULT_COURSE_DAYS
        if item.prescription.issued_at.date() + datetime.timedelta(days=days) >= on:
            active.append(item)
    return active


def alerts(patient, generic=None, product=None, exclude_prescription=None):
    """هشدار حساسیت، تکرار دارو، هم‌گروهی (ATC)، داروی کنترل‌شده و بارداری."""
    messages = []
    names = _names(generic, product)
    for allergy in patient.allergies.filter(is_active=True, kind=Allergy.Kind.DRUG):
        substance = allergy.substance.lower().strip()
        if any(substance in name or name in substance for name in names):
            messages.append(("danger", f"حساسیت ثبت‌شده به {allergy.substance} ({allergy.get_severity_display()})"))
    for entry in patient.history.filter(kind=HistoryEntry.Kind.MEDICATION):
        if any(name in entry.text.lower() for name in names):
            messages.append(("warn", f"بیمار این دارو را از بیرون مصرف می‌کند: {entry.text}"))
    atc = (generic.atc_code if generic else "") or (product.generic.atc_code if product and product.generic_id else "")
    for item in current_medications(patient):
        if exclude_prescription is not None and item.prescription_id == exclude_prescription.pk:
            continue
        other = _names(item.generic, item.product)
        if names & other:
            messages.append(("warn", f"تکرار دارو: {item.label} در نسخه {item.prescription} هنوز در دوره مصرف است"))
            continue
        other_atc = (item.generic.atc_code if item.generic_id else "") or (
            item.product.generic.atc_code if item.product_id and item.product.generic_id else "")
        if atc and other_atc and len(atc) >= 5 and atc[:5] == other_atc[:5]:
            messages.append(("warn", f"داروی هم‌گروه (ATC {atc[:5]}): {item.label} در نسخه {item.prescription}"))
    controlled = (generic is not None and generic.category == "controlled") or (product is not None and product.is_controlled)
    if controlled:
        messages.append(("warn", "داروی تحت کنترل است؛ نسخه مخصوص و ثبت در سامانه مربوط لازم است."))
    age = patient.age_years()
    if patient.sex == "f" and age is not None and 15 <= age <= 50:
        notes = [p.pregnancy_note for p in _products(generic, product) if p.pregnancy_note]
        if notes:
            messages.append(("info", "بیمار در سن باروری است. مصرف در بارداری: " + notes[0][:200]))
    return messages


def _products(generic=None, product=None):
    from apps.shop.models import Product

    if product is not None:
        return [product]
    if generic is not None:
        return list(Product.objects.filter(generic=generic, is_active=True))
    return []


def stock_for(generic=None, product=None):
    """کالاهای داروخانه درمانگاه با موجودی قابل فروش و قیمت (برای پزشک)."""
    return [{"product": p, "stock": p.stock, "price": p.price} for p in _products(generic, product)]


def add_item(prescription, **fields):
    item = PrescriptionItem(prescription=prescription, **fields)
    item.full_clean(exclude=["prescription"])
    item.save()
    return item


def dispense(prescription, user, selections):
    """selections: [(item, product, quantity)]. فاکتور پیش‌نویس صندوق داروساز را می‌سازد؛ پرداخت و ثبت نهایی در صندوق."""
    from apps.sales import cashbox
    from apps.sales import services as sales

    if not prescription.is_valid:
        raise ValidationError("نسخه معتبر نیست.")
    session = cashbox.user_session(user, cashbox.Department.PHARMACY)
    if session is None:
        raise ValidationError("ابتدا شیفت صندوق داروخانه خود را باز کنید.")
    selections = [(item, product, qty) for item, product, qty in selections if qty]
    if not selections:
        raise ValidationError("تعداد هیچ دارویی وارد نشده است.")
    with transaction.atomic():
        invoice = sales.create_pos_invoice(session, user, uuid.uuid4().hex)
        invoice.patient = prescription.patient
        invoice.prescription_number = prescription.insurance_tracking_code or prescription.number
        invoice.prescriber_name = prescription.prescriber.full_name
        invoice.prescriber_council_no = prescription.prescriber.medical_council_number
        invoice.save()
        for item, product, quantity in selections:
            if item.prescription_id != prescription.pk:
                raise ValidationError("ردیف متعلق به این نسخه نیست.")
            if product not in item.candidate_products():
                raise ValidationError(f"«{product.name}» برای «{item.label}» مجاز نیست.")
            if quantity > item.remaining:
                raise ValidationError(f"تعداد «{item.label}» از باقی‌مانده نسخه بیشتر است.")
            if invoice.lines.filter(product=product).exists():
                raise ValidationError(f"«{product.name}» دو بار انتخاب شده است.")
            line = sales.add_line(invoice, product, quantity)
            line.prescription_item = item
            line.save(update_fields=["prescription_item"])
    return invoice
