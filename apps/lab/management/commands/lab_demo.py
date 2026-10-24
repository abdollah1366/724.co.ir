from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.lab import demo


def r(value):
    return f"{value:,}"


class Command(BaseCommand):
    help = "گردش کامل نمونه آزمایشگاه را اجرا و نتیجه را بررسی می‌کند (فقط روی پایگاه داده آزمایشی)."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="رمز کاربران نمونه (برای ورود و دیدن پیشخوان)")

    def handle(self, *args, **options):
        try:
            with transaction.atomic():
                result = demo.run(password=options["password"])
        except demo.DemoAlreadyRun as exc:
            raise CommandError(str(exc)) from exc
        out, order = self.stdout.write, result["order"]
        out(f"پذیرش {order.number}: تعرفه {r(order.gross_amount)} | بیمه پایه {r(order.basic_share)} | "
            f"تکمیلی {r(order.supplementary_share)} | سهم بیمار {r(order.patient_share)} | وضعیت: {order.get_progress_display()}")
        out(f"دریافت از بیمار: {result['payment'].number} · {r(result['payment'].amount)}")
        for claim in result["claims"]:
            out(f"صورت‌حساب {claim.number} ({claim.plan}): {r(claim.total)}")
        out(f"تسویه {result['settlement'].number}: دریافتی {r(result['settlement'].received)} + کسورات {r(result['settlement'].deductions)}")
        out(f"طلب باقی‌مانده از بیمه‌ها: {', '.join(f'{k}: {r(v)}' for k, v in result['ar_insurers'].items()) or 'صفر'}")
        if result["appointment"]:
            out(f"نوبت اینترنتی نمونه: کد پیگیری {result['appointment'].tracking_code}")
        out(f"نوبت حضوری: {result['ticket'].label} · نمایشگر: /lab/queue/display/ · کیوسک: /lab/queue/kiosk/")
        out("کاربران: " + "، ".join(u.username for u in result["users"].values()))
        totals = result["trial_totals"]
        if totals["debit"] != totals["credit"] or order.progress != "ready":
            raise CommandError(f"کنترل ناموفق: تراز {totals} وضعیت {order.progress}")
        self.stdout.write(self.style.SUCCESS("همه کنترل‌ها درست است: تراز برقرار و جواب آماده تحویل."))
