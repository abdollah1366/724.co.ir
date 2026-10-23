from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.backoffice import demo


def r(value):
    return f"{value:,}"


class Command(BaseCommand):
    help = "گردش کامل نمونه خرید، انبار، فروش و حسابداری را اجرا و نتیجه را بررسی می‌کند (فقط روی پایگاه داده آزمایشی)."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="رمز کاربران نمونه (برای ورود و دیدن پیشخوان)")

    def handle(self, *args, **options):
        try:
            with transaction.atomic():
                result = demo.run(password=options["password"])
        except demo.DemoAlreadyRun as exc:
            raise CommandError(str(exc)) from exc
        pnl = result["pnl"]
        out = self.stdout.write
        out("اسناد ثبت‌شده:")
        for name, doc in result["docs"].items():
            for d in doc if isinstance(doc, list) else [doc]:
                out(f"  {name}: {getattr(d, 'number', None) or d}")
        out(f"فروش خالص: {r(pnl['total_revenue'])} | بهای تمام‌شده: {r(pnl['cogs'])} | سود ناخالص: {r(pnl['gross_profit'])} | سود خالص: {r(pnl['net_profit'])}")
        out(f"تراز آزمایشی: بدهکار {r(result['trial_totals']['debit'])} = بستانکار {r(result['trial_totals']['credit'])}")
        out(f"موجودی کالا در دفتر کل {r(result['inventory_gl'])} و در کاردکس {r(result['inventory_ledger'])}")
        out(f"بدهی به تأمین‌کننده: {r(result['supplier_payable'])} | طلب از بیمه: {r(result['ar_insurers'])} | طلب از مشتریان: {r(result['ar_customers'])}")
        out(f"اختلاف صندوق: {r(result['session'].difference)}")
        out(f"هشدارها: {result['alert_counts']}")
        checks = [
            result["trial_totals"]["debit"] == result["trial_totals"]["credit"],
            result["inventory_gl"] == result["inventory_ledger"],
            not result["availability_drift"],
        ]
        if all(checks):
            self.stdout.write(self.style.SUCCESS("همه کنترل‌ها درست است: تراز برقرار، دفتر کل = کاردکس، موجودی قابل فروش = دفتر انبار."))
        else:
            raise CommandError(f"کنترل ناموفق: {checks}")
