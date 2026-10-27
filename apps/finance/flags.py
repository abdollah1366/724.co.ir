"""قابلیت‌های مالی. هسته حسابداری (دفتر کل) critical است چون داروخانه و آزمایشگاه مستقیم روی آن سند می‌زنند."""

from apps.core.flags import register

register("finance", "ماژول مالی", description="صفحات مالی، صندوق درمانگاه و اتصال ماژول‌ها")
register("finance.accounting", "دفتر کل و اسناد", critical=True,
         description="هسته حسابداری؛ داروخانه و آزمایشگاه به آن وابسته‌اند و خاموش نمی‌شود")
register("finance.billing", "صورتحساب و صندوق درمانگاه", description="صدور صورتحساب خدمات، دریافت و استرداد")
register("finance.contracts", "قراردادها")
register("finance.settlements", "تسویه پزشکان", requires=("finance.contracts", "finance.billing"))
register("finance.payroll", "حقوق و دستمزد")
register("finance.integration", "اتصال ماژول‌ها به مالی (رویدادها)")
register("finance.integration.clinic", "اتصال ویزیت درمانگاه", requires=("finance.billing",),
         description="ویزیت امضاشده ← صورتحساب")
register("finance.integration.rehab", "اتصال توانبخشی", requires=("finance.billing",),
         description="جلسه درمان امضاشده ← صورتحساب")
register("finance.integration.telemed", "اتصال مشاوره آنلاین", requires=("finance.billing",),
         description="پرداخت، استرداد و ابطال مشاوره ← صورتحساب، دریافت و سند")
register("finance.integration.pharmacy", "اتصال داروخانه و انبار",
         description="رهگیری فروش، مرجوعی، خرید و اسناد انبار تا سند حسابداری")
register("finance.integration.lab", "اتصال آزمایشگاه",
         description="رهگیری پذیرش، دریافت و تسویه بیمه آزمایشگاه تا سند حسابداری")
