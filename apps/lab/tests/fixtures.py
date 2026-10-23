import datetime
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from apps.accounting.models import Party
from apps.inventory.tests.fixtures import staff  # noqa: F401
from apps.lab.models import InsurancePlan, LabOrder, LabPanel, LabPatient, LabTest
from apps.lab.services import orders as order_service

NATIONAL_CODE = "0499370899"


def seed():
    call_command("seed_lab", "--demo", stdout=StringIO())


def plans(cap=None):
    tamin = Party.objects.create(kind=Party.Kind.INSURER, name="تأمین اجتماعی")
    dana = Party.objects.create(kind=Party.Kind.INSURER, name="بیمه دانا")
    start = timezone.localdate() - datetime.timedelta(days=30)
    basic = InsurancePlan.objects.create(
        insurer=tamin, name="تأمین اجتماعی سرپایی", kind="basic", start_date=start, tariff_percent=60, coverage_percent=70
    )
    supp = InsurancePlan.objects.create(
        insurer=dana, name="دانا تکمیلی", kind="supplementary", start_date=start, coverage_percent=80, max_per_order=cap
    )
    return basic, supp


def patient(**kwargs):
    data = {
        "national_code": NATIONAL_CODE, "first_name": "مریم", "last_name": "رضایی", "sex": "f",
        "birth_date": datetime.date(1985, 3, 1), "mobile": "09121234567",
    }
    data.update(kwargs)
    return LabPatient.objects.create(**data)


def tests(*codes):
    return list(LabTest.objects.filter(code__in=codes))


def make_order(user, p, codes=(), panels=(), post=True, **fields):
    order = LabOrder.objects.create(patient=p, created_by=user, **fields)
    order_service.set_order_tests(order, tests(*codes), list(LabPanel.objects.filter(code__in=panels)))
    if post:
        order.post(user)
    return order
