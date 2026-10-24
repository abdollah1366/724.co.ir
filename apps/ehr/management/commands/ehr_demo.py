from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.ehr import demo


class Command(BaseCommand):
    help = "گردش کامل نمونه پرونده سلامت را اجرا و نتیجه را بررسی می‌کند (فقط روی پایگاه داده آزمایشی)."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="رمز کاربران نمونه (برای ورود و دیدن میز کار)")

    def handle(self, *args, **options):
        try:
            with transaction.atomic():
                result = demo.run(password=options["password"])
        except demo.DemoAlreadyRun as exc:
            raise CommandError(str(exc)) from exc
        out, patient = self.stdout.write, result["patient"]
        out(f"بیمار نمونه: {patient.full_name} · پرونده {patient.file_number} · /ehr/patients/{patient.pk}/")
        out(f"صف: بی‌نوبت {result['walk_in'].label} و نوبت‌دار {result['ticket'].label} · فراخوان فعلی {result['called'].label}"
            " · مانیتور: /ehr/queue/display/?lab=*")
        out(f"نسخه {result['prescription'].number} · یادآورها: "
            + "، ".join(f"{r.get_kind_display()} {r.due_date}" for r in result["reminders"]))
        out("برنامه‌های درمان: " + "، ".join(p.get_discipline_display() for p in result["plans"]))
        out("کاربران: " + "، ".join(u.username for u in result["users"].values()))
        if not (result["encounter_signed"] and result["psych_hidden"] and len(result["reminders"]) == 2):
            raise CommandError("کنترل ناموفق: ویزیت امضا نشده، یادداشت روانشناسی پنهان نیست یا یادآورها ساخته نشده‌اند.")
        self.stdout.write(self.style.SUCCESS("همه کنترل‌ها درست است: ویزیت امضا، یادآورها ساخته و یادداشت روانشناسی محرمانه است."))
