"""فهرست همه پنل‌های کاری سامانه برای صفحه اول پنل مدیریت؛ هر پیوند فقط به کسی نشان داده می‌شود که اجازه باز کردنش را دارد."""

from django.urls import reverse

# (عنوان گروه، [(عنوان پیوند، نام آدرس، مجوزهای لازم: یکی کافی است؛ خالی = همه کارکنان)])
PANELS = [
    ("داروخانه و تجهیزات", [
        ("داشبورد داروخانه", "backoffice:dashboard", ("inventory.view_stockbalance", "sales.view_salesinvoice", "sales.use_pos")),
        ("صندوق فروش", "backoffice:pos", ("sales.use_pos",)),
        ("موجودی انبار", "backoffice:stock", ("inventory.view_stockbalance",)),
        ("هشدارها (کمبود و انقضا)", "backoffice:alerts", ("inventory.view_stockbalance",)),
        ("نسخه‌های درمانگاه", "ehr:pharmacy_queue", ("ehr.dispense_prescription",)),
        ("سود و زیان", "backoffice:pnl", ("accounting.view_financial_reports",)),
        ("تراز آزمایشی", "backoffice:trial_balance", ("accounting.view_financial_reports",)),
        ("بدهی و طلب اشخاص", "backoffice:parties", ("accounting.view_financial_reports",)),
    ]),
    ("درمانگاه و پرونده سلامت", [
        ("جست‌وجوی بیمار", "ehr:search", ("ehr.view_patient",)),
        ("بیماران من (میز کار پزشک)", "ehr:my_patients", ("ehr.view_clinical_record",)),
        ("پذیرش و صف درمانگاه", "ehr:queue_reception", ("ehr.add_clinicticket",)),
        ("ثبت نوبت حضوری", "ehr:reception_book", ("appointments.add_appointment",)),
        ("برنامه‌های درمان (توانبخشی و روانشناسی)", "ehr:my_plans", ("ehr.view_treatmentplan",)),
        ("بیماران مزمن (دیابت و فشار خون)", "ehr:registry", ("ehr.view_all_records",)),
        ("گزارش مشاهده پرونده‌ها", "ehr:access_log", ("ehr.view_recordaccess",)),
        ("گزارش مدیریتی درمانگاه", "ehr:report", ("ehr.view_management_report", "ehr.view_all_records")),
        ("نمایشگر صف درمانگاه", "ehr:queue_display", ()),
    ]),
    ("مشاوره و ویزیت آنلاین", [
        ("کارتابل مشاوره آنلاین", "telemed:inbox", ("telemed.answer_consultation", "telemed.answer_pharmacy")),
        ("تنظیم مشاوره آنلاین پزشکان", "admin:telemed_doctoronlineprofile_changelist", ("telemed.change_doctoronlineprofile",)),
        ("پرداخت‌ها و استردادها", "admin:telemed_consultpayment_changelist", ("telemed.view_consultpayment",)),
        ("گزارش مشاوره آنلاین", "telemed:report", ("telemed.view_telemed_report",)),
    ]),
    ("آزمایشگاه", [
        ("داشبورد آزمایشگاه", "lab:staff_dashboard", ("lab.view_laborder",)),
        ("پذیرش", "lab:staff_reception", ("lab.add_laborder",)),
        ("پذیرش‌ها", "lab:staff_orders", ("lab.view_laborder",)),
        ("درخواست‌های آزمایش پزشکان", "ehr:lab_request_queue", ("lab.add_laborder",)),
        ("نوبت‌های نمونه‌گیری", "lab:staff_appointments", ("lab.view_samplingappointment",)),
        ("کارتابل جواب‌ها", "lab:staff_worklist", ("lab.view_labresult",)),
        ("کنترل کیفی", "lab:staff_qc", ("lab.add_qcresult",)),
        ("صف و فراخوان باجه", "lab:queue_console", ("lab.change_queueticket",)),
        ("کیوسک نوبت", "lab:kiosk", ("lab.add_queueticket",)),
        ("نمایشگر صف آزمایشگاه", "lab:queue_display", ()),
        ("بیمه و صورت‌حساب‌ها", "lab:staff_claims", ("lab.view_insuranceclaim",)),
        ("گزارش صندوق", "lab:staff_cashier", ("lab.view_labpayment",)),
        ("گزارش‌های آزمایشگاه", "lab:staff_reports", ("lab.view_laborder",)),
        ("مشاوره و ارتباط با مشتری", "lab:crm_inbox", ("lab.view_consultrequest",)),
    ]),
    ("مالی و حسابداری", [
        ("داشبورد مالی", "finance:dashboard", ("accounting.view_financial_reports", "finance.use_cashier")),
        ("صندوق درمانگاه", "finance:cashier", ("finance.use_cashier",)),
        ("صورتحساب‌های خدمات", "finance:invoices", ("finance.view_invoice",)),
        ("مطالبات بیماران", "finance:receivables", ("accounting.view_financial_reports",)),
        ("دفتر حساب و روزنامه", "finance:ledger", ("accounting.view_financial_reports",)),
        ("سود و زیان بخش‌ها", "finance:center_pnl", ("accounting.view_financial_reports",)),
        ("قراردادها", "admin:finance_contract_changelist", ("finance.view_contract",)),
        ("تسویه پزشکان", "admin:finance_providersettlement_changelist", ("finance.view_providersettlement",)),
        ("حقوق و دستمزد", "admin:finance_payrollrun_changelist", ("finance.view_payrollrun",)),
        ("اسناد دستی و دوره‌های مالی", "admin:accounting_manualjournal_changelist", ("accounting.view_manualjournal",)),
        ("کنترل‌های مالی", "finance:checks", ("accounting.view_financial_reports",)),
        ("اتصال ماژول‌ها (رویدادها)", "finance:integration", ("core.view_outboxevent",)),
        ("تنظیمات ماژول‌ها", "finance:settings", ("core.change_featureflag",)),
    ]),
    ("وبلاگ", [
        ("پیشخوان وبلاگ", "blog:manage", ("blog.view_post",)),
        ("مقاله جدید", "admin:blog_post_add", ("blog.add_post",)),
    ]),
    ("مدیریت مجموعه", [
        ("کارکنان و نقش‌ها", "backoffice:staff", ("auth.view_user", "blog.manage_blog_staff")),
        ("صندوق‌ها (تعریف و وضعیت)", "backoffice:cashboxes", ("sales.view_cashregister", "sales.change_cashregister")),
        ("شیفت صندوق من", "backoffice:my_cashbox", ("sales.use_pos", "finance.use_cashier", "lab.post_labpayment")),
        ("گزارش ممیزی (چه کسی چه کرد)", "admin:audit_auditlog_changelist", ("audit.view_auditlog",)),
        ("صفحه اصلی سایت", "core:home", ()),
    ]),
]


# نماد و رنگ هر گروه در منوی کناری و کاشی‌های پیشخوان
GROUP_STYLE = {
    "داروخانه و تجهیزات": ("💊", "teal"),
    "درمانگاه و پرونده سلامت": ("🩺", "blue"),
    "مشاوره و ویزیت آنلاین": ("💬", "violet"),
    "آزمایشگاه": ("🧪", "pink"),
    "مالی و حسابداری": ("💰", "amber"),
    "وبلاگ": ("📝", "green"),
    "مدیریت مجموعه": ("⚙️", "slate"),
}

# نماد اختصاصی پیوندهایی که در «دسترسی سریع» زیاد دیده می‌شوند؛ بقیه نماد گروهشان را می‌گیرند.
LINK_ICONS = {
    "backoffice:dashboard": "📊",
    "backoffice:pos": "🧾",
    "backoffice:stock": "📦",
    "backoffice:alerts": "⏰",
    "ehr:search": "🔍",
    "ehr:my_patients": "👨‍⚕️",
    "ehr:queue_reception": "🎫",
    "ehr:reception_book": "📅",
    "telemed:inbox": "💬",
    "lab:staff_reception": "🧪",
    "lab:staff_worklist": "🔬",
    "lab:staff_dashboard": "🧫",
    "finance:cashier": "💵",
    "finance:dashboard": "📈",
    "finance:invoices": "🧾",
    "blog:manage": "📝",
    "backoffice:staff": "👥",
    "backoffice:cashboxes": "🏦",
    "backoffice:my_cashbox": "💵",
}

# هفت منوی پرکاربرد پیش‌فرض برای «دسترسی سریع»، به ترتیب اهمیت کار روزانه مجموعه.
# تا وقتی کارمند سابقه استفاده ندارد همین‌ها (هرکدام که اجازه‌اش را دارد) نشان داده می‌شوند؛
# بعد منوهایی که خودش بیشتر باز می‌کند جلو می‌آیند (MenuUsage).
QUICK_DEFAULTS = [
    "backoffice:dashboard",
    "backoffice:pos",
    "ehr:queue_reception",
    "ehr:search",
    "lab:staff_reception",
    "telemed:inbox",
    "finance:cashier",
]
QUICK_COUNT = 7
# صفحه اصلی سایت و نمایشگرهای تمام‌صفحه صف جای «دسترسی سریع» نیستند.
QUICK_EXCLUDE = {"core:home", "ehr:queue_display", "lab:queue_display"}


def _allowed(user, perms):
    return user.is_superuser or not perms or any(user.has_perm(p) for p in perms)


def panels_for(user):
    groups = []
    for title, links in PANELS:
        allowed = [(label, reverse(name)) for label, name, perms in links if _allowed(user, perms)]
        if allowed:
            groups.append((title, allowed))
    return groups


def menu_names():
    """نام همه آدرس‌های منوی پنل؛ میان‌افزار فقط بازکردن همین‌ها را برای دسترسی سریع می‌شمارد."""
    return {name for _, links in PANELS for _, name, _ in links} - QUICK_EXCLUDE


def menu_for(user, path=""):
    """منوی کناری پنل کارکنان: گروه‌ها با نماد و رنگ؛ پیوندی که صفحه جاری زیر آن است فعال می‌شود."""
    groups = []
    for title, links in PANELS:
        icon, color = GROUP_STYLE.get(title, ("•", "slate"))
        items = [{"label": label, "name": name, "url": reverse(name), "icon": LINK_ICONS.get(name, icon)}
                 for label, name, perms in links if _allowed(user, perms)]
        if items:
            groups.append({"title": title, "icon": icon, "color": color, "links": items, "active": False})
    # بلندترین پیشوند آدرس برنده است: /lab/staff/orders/12/ زیر «پذیرش‌ها» است، نه «داشبورد».
    best = None
    for group in groups:
        for item in group["links"]:
            url = item["url"]
            if url != "/" and path.startswith(url) and (best is None or len(url) > len(best[1]["url"])):
                best = (group, item)
    if best:
        best[0]["active"] = best[1]["active"] = True
    return groups


def quick_links(user, groups=None):
    """هفت منوی پرکاربرد این کارمند: اول پراستفاده‌ترین‌های خودش، بعد پیش‌فرض‌ها، بعد بقیه مجازها."""
    from .models import MenuUsage

    groups = groups if groups is not None else menu_for(user)
    allowed = {}
    for group in groups:
        for item in group["links"]:
            allowed[item["name"]] = dict(item, color=group["color"], group=group["title"])
    used = MenuUsage.objects.filter(user=user, url_name__in=allowed).order_by("-count", "-last_used")
    order = [u.url_name for u in used] + QUICK_DEFAULTS + list(allowed)
    picked = []
    for name in order:
        if name in allowed and name not in picked and name not in QUICK_EXCLUDE:
            picked.append(name)
        if len(picked) == QUICK_COUNT:
            break
    return [allowed[name] for name in picked]
