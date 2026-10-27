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
    ("وبلاگ", [
        ("پیشخوان وبلاگ", "blog:manage", ("blog.view_post",)),
        ("مقاله جدید", "admin:blog_post_add", ("blog.add_post",)),
    ]),
    ("مدیریت مجموعه", [
        ("کارکنان و نقش‌ها", "backoffice:staff", ("auth.view_user", "blog.manage_blog_staff")),
        ("گزارش ممیزی (چه کسی چه کرد)", "admin:audit_auditlog_changelist", ("audit.view_auditlog",)),
        ("صفحه اصلی سایت", "core:home", ()),
    ]),
]


def panels_for(user):
    groups = []
    for title, links in PANELS:
        allowed = [(label, reverse(name)) for label, name, perms in links
                   if user.is_superuser or not perms or any(user.has_perm(p) for p in perms)]
        if allowed:
            groups.append((title, allowed))
    return groups
