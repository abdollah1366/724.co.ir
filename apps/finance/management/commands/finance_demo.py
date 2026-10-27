from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.finance import demo


class Command(BaseCommand):
    help = "گردش نمونه کامل مالی (صورتحساب، دریافت، استرداد، قرارداد، تسویه، حقوق) با داده ساختگی؛ فقط روی پایگاه داده آزمایشی."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="رمز کاربران نمونه (برای ورود و دیدن صفحات)")

    def handle(self, *args, **options):
        try:
            with transaction.atomic():
                result = demo.run(password=options["password"])
        except demo.DemoAlreadyRun as exc:
            raise CommandError(str(exc)) from exc
        out = self.stdout.write
        for name, doc in result["docs"].items():
            for d in doc if isinstance(doc, list) else [doc]:
                out(f"  {name}: {d.number}")
        invoice = result["invoice"]
        out(f"صورتحساب: ناخالص {invoice.gross:,} | تخفیف {invoice.discount:,} | سهم بیمه {invoice.insurer_share:,} | "
            f"سهم بیمار {invoice.patient_share:,} | پرداخت‌شده {invoice.paid_amount:,} | مانده {invoice.balance_due:,}")
        out(f"علی‌الحساب بیمار: {result['advance']:,} | تکرار دریافت همان رسید را برگرداند: {result['idempotent_receipt']}")
        out(f"تسویه پزشک: {result['settlement_total']:,} | خالص حقوق: {result['payroll_net']:,}")
        out(f"تراز آزمایشی: بدهکار {result['trial']['debit']:,} = بستانکار {result['trial']['credit']:,}")
        failed = [c for c in result["checks"] if not c["ok"]]
        for check in result["checks"]:
            out(f"  {'✓' if check['ok'] else '✗'} {check['name']}: {check['detail']}")
        if failed or not result["idempotent_receipt"]:
            raise CommandError("گردش نمونه مالی مغایرت دارد.")
        self.stdout.write(self.style.SUCCESS("گردش نمونه مالی بدون مغایرت انجام شد."))
