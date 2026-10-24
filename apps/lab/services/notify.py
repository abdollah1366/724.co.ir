"""پیامک‌های آزمایشگاه. متن پیامک هیچ اطلاعات پزشکی (نام آزمایش یا جواب) ندارد."""

import jdatetime
from django.conf import settings
from django.urls import reverse

from apps.core.sms import send_sms


def _url(name):
    return settings.SITE_URL + reverse(name)


def _lab():
    return settings.LAB_INFO["name"]


def result_ready(order):
    if order.delivery != order.Delivery.ONLINE:
        text = f"{order.patient.first_name} عزیز، جواب پذیرش {order.number} آماده است. لطفاً برای دریافت حضوری مراجعه کنید. {_lab()}"
    else:
        text = (
            f"{order.patient.first_name} عزیز، جواب پذیرش {order.number} آماده است. "
            f"دریافت با کد ملی و کد یک‌بارمصرف: {_url('lab:result_lookup')} {_lab()}"
        )
    return send_sms(order.patient.mobile, text, "lab_result_ready")


def otp(mobile, code):
    return send_sms(mobile, f"کد ورود به جواب آزمایش: {code}\nاین کد را به هیچ‌کس ندهید. {_lab()}", "lab_otp")


def _when(appointment):
    return f"{jdatetime.date.fromgregorian(date=appointment.date).strftime('%Y/%m/%d')} ساعت {appointment.time:%H:%M}"


def appointment_booked(appointment):
    return send_sms(
        appointment.mobile,
        f"نوبت نمونه‌گیری شما برای {_when(appointment)} ثبت شد. کد پیگیری: {appointment.tracking_code} {_lab()}",
        "lab_booking",
    )


def appointment_reminder(appointment):
    return send_sms(
        appointment.mobile,
        f"یادآوری نوبت نمونه‌گیری: {_when(appointment)}. شرایط آمادگی: {_url('lab:preparation')} {_lab()}",
        "lab_reminder",
    )


def recall_reminder(patient):
    return send_sms(
        patient.mobile,
        f"{patient.first_name} عزیز، زمان آزمایش دوره‌ای شما طبق برنامه پزشک فرا رسیده است. نوبت: {_url('lab:book')} "
        f"(لغو یادآوری با تماس با آزمایشگاه) {_lab()}",
        "lab_recall",
    )


def feedback_request(order, url):
    return send_sms(order.patient.mobile, f"نظر شما درباره خدمات {_lab()} برای ما مهم است: {url}", "lab_feedback")
