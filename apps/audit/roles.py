"""نقش‌های کارکنان و مجوزهای هر نقش (گروه‌های Django). با هر migrate به‌روز می‌شوند.

مدیر کل (superuser) همه مجوزها را دارد. کاربران کارکنان باید is_staff داشته باشند و عضو یک یا چند نقش شوند.
"""

from django.contrib.auth.models import Group, Permission

VIEW = "view"
EDIT = ("add", "change", "delete", "view")

MASTER_DATA = ["shop.product", "shop.category", "shop.unit", "shop.productpackage"]

ROLES = {
    "مدیر داروخانه": {
        "models": {
            **{m: EDIT for m in MASTER_DATA},
            "inventory.warehouse": EDIT, "inventory.location": EDIT, "inventory.batch": (VIEW, "change"),
            "inventory.inventorydocument": EDIT, "inventory.inventorydocumentline": EDIT,
            "purchasing.purchaseorder": EDIT, "purchasing.goodsreceipt": EDIT, "purchasing.purchaseinvoice": EDIT,
            "sales.salesinvoice": EDIT, "sales.salesreturn": EDIT, "sales.cashsession": EDIT, "sales.cashregister": EDIT,
            "accounting.party": EDIT, "accounting.account": EDIT, "accounting.payment": EDIT, "accounting.expense": EDIT,
            "accounting.journalentry": (VIEW,), "inventory.stockmove": (VIEW,), "inventory.stockbalance": (VIEW,),
            "shop.order": EDIT, "shop.pricehistory": (VIEW,), "audit.auditlog": (VIEW,),
            "inventory.batchstatuschange": (VIEW,),
        },
        "extra": [
            "inventory.post_inventorydocument", "inventory.reverse_inventorydocument",
            "purchasing.post_purchaseorder", "purchasing.reverse_purchaseorder", "purchasing.post_goodsreceipt",
            "purchasing.reverse_goodsreceipt", "purchasing.post_purchaseinvoice", "purchasing.reverse_purchaseinvoice",
            "sales.post_salesinvoice", "sales.reverse_salesinvoice", "sales.post_salesreturn", "sales.use_pos",
            "accounting.post_payment", "accounting.reverse_payment", "accounting.post_expense", "accounting.reverse_expense",
            "accounting.view_financial_reports", "inventory.change_batch_status",
        ],
    },
    "داروساز مسئول فنی": {
        "models": {
            **{m: EDIT for m in MASTER_DATA},
            "inventory.batch": (VIEW,), "inventory.batchstatuschange": (VIEW,), "inventory.stockbalance": (VIEW,),
            "inventory.stockmove": (VIEW,), "sales.salesinvoice": ("add", "change", "view"), "sales.salesreturn": ("add", "change", "view"),
            "shop.order": ("view", "change"), "shop.pharmacyquestion": ("view", "change"),
            "inventory.inventorydocument": ("add", "change", "view"), "inventory.inventorydocumentline": ("add", "change", "delete", "view"),
            "ehr.prescription": (VIEW,), "ehr.prescriptionitem": (VIEW,), "ehr.drugcounseling": ("add", VIEW),
            "ehr.genericdrug": EDIT,
        },
        "extra": [
            "sales.approve_prescription", "sales.post_salesinvoice", "sales.post_salesreturn", "sales.use_pos",
            "inventory.change_batch_status", "inventory.post_inventorydocument", "ehr.dispense_prescription",
        ],
    },
    "انباردار": {
        "models": {
            "shop.product": (VIEW,), "inventory.warehouse": (VIEW,), "inventory.location": EDIT, "inventory.batch": (VIEW,),
            "inventory.stockbalance": (VIEW,), "inventory.stockmove": (VIEW,),
            "inventory.inventorydocument": ("add", "change", "view"), "inventory.inventorydocumentline": ("add", "change", "delete", "view"),
            "purchasing.goodsreceipt": ("add", "change", "view"), "purchasing.goodsreceiptline": ("add", "change", "delete", "view"),
            "purchasing.purchaseorder": (VIEW,),
        },
        "extra": ["inventory.post_inventorydocument", "purchasing.post_goodsreceipt"],
    },
    "کارشناس خرید": {
        "models": {
            "shop.product": (VIEW,), "accounting.party": ("add", "change", "view"),
            "purchasing.purchaseorder": ("add", "change", "view"), "purchasing.purchaseorderline": ("add", "change", "delete", "view"),
            "purchasing.goodsreceipt": (VIEW,), "purchasing.purchaseinvoice": ("add", "change", "view"),
            "purchasing.purchaseinvoiceline": ("add", "change", "delete", "view"),
        },
        "extra": ["purchasing.post_purchaseorder"],
    },
    "صندوق‌دار": {
        "models": {
            "shop.product": (VIEW,), "sales.salesinvoice": ("add", "change", "view"), "sales.cashsession": ("add", "view"),
            "sales.salesreturn": ("add", "change", "view"), "accounting.party": ("add", "view"), "shop.order": (VIEW,),
        },
        "extra": ["sales.post_salesinvoice", "sales.use_pos"],
    },
    "حسابدار": {
        "models": {
            "accounting.party": EDIT, "accounting.account": EDIT, "accounting.payment": EDIT, "accounting.expense": EDIT,
            "accounting.journalentry": (VIEW,), "accounting.journalline": (VIEW,), "purchasing.purchaseinvoice": ("add", "change", "view"),
            "purchasing.purchaseinvoiceline": ("add", "change", "delete", "view"), "purchasing.goodsreceipt": (VIEW,),
            "sales.salesinvoice": (VIEW,), "sales.salesreturn": (VIEW,), "sales.cashsession": (VIEW,),
            "inventory.stockmove": (VIEW,), "shop.pricehistory": (VIEW,),
        },
        "extra": [
            "purchasing.post_purchaseinvoice", "purchasing.reverse_purchaseinvoice", "accounting.post_payment",
            "accounting.reverse_payment", "accounting.post_expense", "accounting.reverse_expense",
            "accounting.view_financial_reports", "sales.reverse_salesinvoice",
        ],
    },
}

LAB_CATALOG = ["lab.labsection", "lab.specimentype", "lab.prepinstruction", "lab.labtest", "lab.referencerange",
               "lab.labpanel", "lab.screeningrule", "lab.samplingschedule", "lab.queueservice", "lab.queuecounter"]
LAB_QUEUE = {"lab.queueticket": ("add", "change", VIEW), "lab.queueservice": (VIEW,), "lab.queuecounter": (VIEW,)}
LAB_RESULTS = {
    "lab.laborder": (VIEW,), "lab.labpatient": (VIEW,), "lab.specimen": (VIEW, "change"), "lab.labresult": (VIEW, "change"),
    "lab.criticalnotification": ("add", VIEW), "lab.qcmaterial": (VIEW,), "lab.qcresult": ("add", VIEW),
    "lab.resultrevision": (VIEW,), "lab.labtest": (VIEW,),
}
LAB_RECEPTION = {
    "lab.labpatient": ("add", "change", VIEW), "lab.laborder": ("add", "change", VIEW), "lab.laborderitem": EDIT,
    "lab.samplingappointment": ("add", "change", VIEW), "lab.labpayment": ("add", VIEW), "lab.specimen": (VIEW, "change"),
    "lab.labresult": (VIEW,), "lab.labtest": (VIEW,), "lab.labpanel": (VIEW,), "lab.insuranceplan": (VIEW,), **LAB_QUEUE,
}
LAB_INSURANCE = {
    "lab.insuranceplan": EDIT, "lab.plantestrule": EDIT, "lab.insuranceclaim": EDIT, "lab.claimline": EDIT,
    "lab.claimsettlement": ("add", VIEW), "lab.laborder": (VIEW,), "accounting.party": ("add", "change", VIEW),
}

ROLES.update({
    "پذیرش آزمایشگاه": {
        "models": {**LAB_RECEPTION, "ehr.labrequest": ("change", VIEW)},
        "extra": ["lab.post_laborder", "lab.post_labpayment", "lab.deliver_lab_report"],
    },
    "نمونه‌گیر آزمایشگاه": {
        "models": {"lab.laborder": (VIEW,), "lab.specimen": (VIEW, "change"), "lab.samplingappointment": (VIEW,), **LAB_QUEUE},
        "extra": [],
    },
    "کارشناس آزمایشگاه": {"models": LAB_RESULTS, "extra": ["lab.enter_labresult"]},
    "مسئول فنی آزمایشگاه": {
        "models": {
            **LAB_RESULTS, **{m: EDIT for m in LAB_CATALOG}, "lab.qcmaterial": EDIT, "lab.instrument": EDIT,
            "lab.laborder": ("change", VIEW), "audit.auditlog": (VIEW,),
        },
        "extra": [
            "lab.enter_labresult", "lab.approve_labresult", "lab.amend_labresult", "lab.reverse_laborder",
            "lab.deliver_lab_report",
        ],
    },
    "کارشناس بیمه آزمایشگاه": {
        "models": LAB_INSURANCE,
        "extra": ["lab.post_insuranceclaim", "lab.reverse_insuranceclaim", "lab.post_claimsettlement",
                  "lab.reverse_claimsettlement"],
    },
    "مشاور آزمایشگاه": {
        "models": {
            "lab.consultrequest": ("add", "change", VIEW), "lab.interaction": ("add", VIEW), "lab.feedback": (VIEW,),
            "lab.laborder": (VIEW,), "lab.labtest": (VIEW,), "lab.labpanel": (VIEW,), "lab.insuranceplan": (VIEW,),
            "lab.samplingappointment": ("add", "change", VIEW),
        },
        "extra": [],
    },
    "کیوسک نوبت": {"models": {"lab.queueticket": ("add",), "lab.queueservice": (VIEW,)}, "extra": []},
    "مدیر آزمایشگاه": {
        "models": {
            **LAB_RECEPTION, **LAB_INSURANCE, **{m: EDIT for m in LAB_CATALOG}, "lab.labpayment": (VIEW,),
            "lab.qcmaterial": EDIT, "lab.qcresult": (VIEW,), "lab.instrument": EDIT, "audit.auditlog": (VIEW,),
            "lab.consultrequest": ("add", "change", VIEW), "lab.interaction": ("add", VIEW), "lab.feedback": (VIEW,),
        },
        "extra": [
            "lab.post_laborder", "lab.reverse_laborder", "lab.give_lab_discount", "lab.post_labpayment",
            "lab.reverse_labpayment", "lab.deliver_lab_report", "lab.post_insuranceclaim", "lab.reverse_insuranceclaim",
            "lab.post_claimsettlement", "lab.reverse_claimsettlement", "accounting.view_financial_reports",
        ],
    },
})

CLINIC_FRONT_DESK = {
    "ehr.patient": ("add", "change", VIEW), "appointments.appointment": ("add", "change", VIEW),
    "ehr.clinicticket": ("add", "change", VIEW), "clinic.doctor": (VIEW,),
}
EHR_CLINICAL = {"ehr.patient": (VIEW,), "appointments.appointment": (VIEW,), "lab.labtest": (VIEW,), "ehr.icd10code": (VIEW,)}
EHR_DOCTOR = {
    **EHR_CLINICAL, "ehr.encounter": ("add", "change", VIEW), "ehr.encounteraddendum": ("add", VIEW),
    "ehr.vitalsigns": ("add", VIEW), "ehr.diagnosis": EDIT, "ehr.problem": ("add", "change", VIEW),
    "ehr.allergy": ("add", "change", VIEW), "ehr.historyentry": ("add", "change", VIEW), "ehr.referral": ("add", "change", VIEW),
    "ehr.encounterservice": EDIT, "ehr.servicecode": (VIEW,), "ehr.genericdrug": (VIEW,),
    "ehr.prescription": ("add", "change", VIEW), "ehr.prescriptionitem": EDIT, "ehr.drugcounseling": ("add", VIEW),
    "ehr.labrequest": ("add", "change", VIEW), "ehr.externaldocument": ("add", "change", VIEW), "ehr.documentfile": ("add", VIEW),
    "ehr.externallabvalue": ("add", VIEW),
    "ehr.treatmentplan": (VIEW,), "ehr.therapysession": (VIEW,), "ehr.assessment": (VIEW,),
    "ehr.reminder": ("add", "change", VIEW), "ehr.clinicticket": ("change", VIEW),
}
EHR_THERAPIST = {
    **EHR_CLINICAL, "ehr.encounter": (VIEW,), "ehr.vitalsigns": ("add", VIEW), "ehr.diagnosis": (VIEW,),
    "ehr.problem": (VIEW,), "ehr.allergy": ("add", VIEW), "ehr.historyentry": ("add", VIEW), "ehr.referral": ("add", VIEW),
    "ehr.servicecode": (VIEW,), "ehr.externaldocument": ("add", VIEW), "ehr.documentfile": ("add", VIEW),
    "ehr.externallabvalue": (VIEW,), "ehr.labrequest": (VIEW,), "ehr.prescription": (VIEW,),
    "ehr.treatmentplan": ("add", "change", VIEW), "ehr.therapysession": ("add", VIEW), "ehr.assessment": ("add", VIEW),
    "ehr.reminder": ("add", "change", VIEW), "ehr.clinicticket": ("change", VIEW),
}
EHR_NURSE = {
    **EHR_CLINICAL, "ehr.encounter": (VIEW,), "ehr.vitalsigns": ("add", VIEW), "ehr.diagnosis": (VIEW,),
    "ehr.problem": (VIEW,), "ehr.allergy": ("add", VIEW), "ehr.historyentry": ("add", VIEW), "ehr.referral": (VIEW,),
    "ehr.externaldocument": ("add", VIEW), "ehr.documentfile": ("add", VIEW), "ehr.externallabvalue": ("add", VIEW),
    "ehr.labrequest": (VIEW,), "ehr.clinicticket": ("change", VIEW),
}

ROLES.update({
    "پزشک": {"models": EHR_DOCTOR, "extra": ["ehr.view_clinical_record", "ehr.sign_encounter"]},
    "پرستار": {"models": EHR_NURSE, "extra": ["ehr.view_clinical_record"]},
    # فیزیوتراپیست، گفتاردرمانگر، کاردرمانگر، شنوایی‌شناس و کارشناس تغذیه
    "درمانگر توانبخشی": {"models": EHR_THERAPIST, "extra": ["ehr.view_clinical_record"]},
    # یادداشت‌های روانشناسی به‌طور پیش‌فرض فقط برای خود درمانگر قابل خواندن است (TreatmentPlan.can_read_notes)
    "روانشناس": {"models": EHR_THERAPIST, "extra": ["ehr.view_clinical_record"]},
    "پذیرش درمانگاه": {"models": CLINIC_FRONT_DESK, "extra": []},
    "مسئول فنی درمانگاه": {
        "models": {
            **EHR_DOCTOR, "ehr.patient": ("add", "change", VIEW), "ehr.recordaccess": (VIEW,), "audit.auditlog": (VIEW,),
            "clinic.doctor": ("change", VIEW), "ehr.icd10code": EDIT,
            "ehr.servicecode": EDIT, "ehr.rvucoefficient": EDIT, "ehr.genericdrug": EDIT,
            "ehr.treatmentplan": ("change", VIEW), "ehr.clinicticket": ("add", "change", VIEW),
        },
        "extra": ["ehr.view_clinical_record", "ehr.view_all_records"],
    },
})


ROLES.update({
    # منشی مطب: نوبت، پذیرش و صف پزشکان؛ مثل پذیرش فقط مشخصات هویتی را می‌بیند، نه سوابق بالینی.
    "منشی پزشک": {"models": {**CLINIC_FRONT_DESK, "clinic.workinghours": (VIEW,)}, "extra": []},
    # مدیر کل مجموعه: همه بخش‌های مالی، انبار، آزمایشگاه (بدون جواب‌ها)، نوبت‌دهی، کارکنان و گزارش‌ها.
    # طبق الزام محرمانگی، پرونده پزشکی و جواب آزمایش بیماران را نمی‌بیند؛ گزارش درمانگاه فقط آماری است.
})

# داده‌های پزشکی که مدیر کل (طبق الزام محرمانگی) نمی‌بیند
CLINICAL_DATA = {"lab.labresult", "lab.specimen", "lab.resultrevision", "lab.criticalnotification", "lab.qcresult"}
CLINICAL_EXTRA = {"lab.enter_labresult", "lab.approve_labresult", "lab.amend_labresult", "lab.deliver_lab_report"}


def _merge(names, base):
    models, extra = {}, set()
    for name in names:
        for label, actions in ROLES[name]["models"].items():
            if label not in CLINICAL_DATA:
                models[label] = tuple(sorted(set(models.get(label, ())) | set(actions)))
        extra |= set(ROLES[name]["extra"]) - CLINICAL_EXTRA
    for label, actions in base["models"].items():
        models[label] = tuple(sorted(set(models.get(label, ())) | set(actions)))
    return {"models": models, "extra": sorted(extra | set(base["extra"]))}


# مدیر کل مجموعه: اجتماع همه نقش‌های داروخانه، انبار، حسابداری، پذیرش و مدیریت آزمایشگاه و منشی، به‌علاوه کارکنان و
# گزارش آماری درمانگاه؛ پرونده پزشکی و جواب آزمایش بیماران را نمی‌بیند.
ROLES["مدیر کل"] = _merge(
    ["مدیر داروخانه", "انباردار", "کارشناس خرید", "صندوق‌دار", "حسابدار", "مدیر آزمایشگاه", "پذیرش آزمایشگاه",
     "کارشناس بیمه آزمایشگاه", "مشاور آزمایشگاه", "کیوسک نوبت", "منشی پزشک", "پذیرش درمانگاه"],
    {"models": {"clinic.doctor": ("add", "change", VIEW), "clinic.department": EDIT, "clinic.service": EDIT,
                "clinic.specialty": EDIT, "clinic.workinghours": EDIT, "auth.user": ("add", "change", VIEW),
                "auth.group": (VIEW,), "core.smsmessage": (VIEW,), "ehr.recordaccess": (VIEW,), "ehr.rvucoefficient": EDIT,
                "ehr.servicecode": (VIEW,), "audit.auditlog": (VIEW,)},
     "extra": ["ehr.view_management_report"]},
)

# گروه‌بندی نقش‌ها در صفحه مدیریت کارکنان
ROLE_AREAS = [
    ("مدیریت", ["مدیر کل"]),
    ("داروخانه و انبار", ["مدیر داروخانه", "داروساز مسئول فنی", "انباردار", "کارشناس خرید", "صندوق‌دار", "حسابدار"]),
    ("درمانگاه", ["مسئول فنی درمانگاه", "پزشک", "پرستار", "درمانگر توانبخشی", "روانشناس", "منشی پزشک", "پذیرش درمانگاه"]),
    ("آزمایشگاه", ["مدیر آزمایشگاه", "مسئول فنی آزمایشگاه", "کارشناس آزمایشگاه", "نمونه‌گیر آزمایشگاه", "پذیرش آزمایشگاه",
                   "کارشناس بیمه آزمایشگاه", "مشاور آزمایشگاه", "کیوسک نوبت"]),
]


def role_permissions(spec):
    names = []
    for label, actions in spec["models"].items():
        app, model = label.split(".")
        names += [f"{app}.{action}_{model}" for action in actions]
    return names + list(spec["extra"])


def _get(name):
    app, codename = name.split(".")
    return Permission.objects.get(content_type__app_label=app, codename=codename)


def setup_roles():
    """ساخت/به‌روزرسانی گروه‌ها. مجوز ناموجود خطا می‌دهد تا اشتباه تایپی پنهان نماند."""
    for role, spec in ROLES.items():
        group, _ = Group.objects.get_or_create(name=role)
        group.permissions.set([_get(name) for name in role_permissions(spec)])


def setup_roles_after_migrate(sender, app_config=None, **kwargs):
    if app_config is None or app_config.label != "audit":
        return
    try:
        setup_roles()
    except Permission.DoesNotExist:
        # در اولین migrate برخی مجوزها هنوز ساخته نشده‌اند؛ اجرای بعدی یا دستور setup_roles کامل می‌کند.
        pass


ROLE_DESCRIPTIONS = {
    "مدیر کل": "همه بخش‌های مالی، انبار، آزمایشگاه، نوبت‌دهی، کارکنان و گزارش‌های آماری؛ بدون دسترسی به پرونده پزشکی و جواب آزمایش",
    "مدیر داروخانه": "کالا، خرید، انبار، فروش، صندوق، حسابداری و گزارش مالی داروخانه",
    "داروساز مسئول فنی": "تأیید فروش نسخه‌ای، تحویل نسخه درمانگاه، مشاوره دارویی، وضعیت سری ساخت",
    "انباردار": "رسید کالا، انتقال و شمارش انبار",
    "کارشناس خرید": "سفارش و فاکتور خرید، تأمین‌کنندگان",
    "صندوق‌دار": "صندوق فروش و مرجوعی",
    "حسابدار": "دریافت و پرداخت، هزینه‌ها، فاکتور خرید، گزارش‌های مالی",
    "مسئول فنی درمانگاه": "همه پرونده‌های پزشکی، گزارش مشاهده پرونده‌ها، کاتالوگ‌ها و ضرایب تعرفه",
    "پزشک": "پرونده بیماران خودش، ویزیت، نسخه، درخواست آزمایش، ارجاع، یادآور، صف مطب خودش",
    "پرستار": "علائم حیاتی و حساسیت بیماران امروز، مدارک، فراخوان صف",
    "درمانگر توانبخشی": "فیزیوتراپی، گفتاردرمانی، کاردرمانی، شنوایی‌شناسی و تغذیه: برنامه درمان، جلسه و ارزیابی؛ بدون نسخه دارو",
    "روانشناس": "مثل درمانگر توانبخشی؛ یادداشت‌ها پیش‌فرض فقط برای خودش قابل خواندن است",
    "منشی پزشک": "نوبت‌دهی، پذیرش و صف پزشکان، مشخصات هویتی بیمار؛ بدون سوابق پزشکی",
    "پذیرش درمانگاه": "پذیرش بیمار، نوبت و شماره صف؛ بدون سوابق پزشکی",
    "مدیر آزمایشگاه": "پذیرش، صندوق، بیمه، کاتالوگ و گزارش‌های آزمایشگاه",
    "مسئول فنی آزمایشگاه": "تأیید و اصلاح جواب، کنترل کیفی، کاتالوگ و محدوده مرجع",
    "کارشناس آزمایشگاه": "ورود جواب و کنترل کیفی",
    "نمونه‌گیر آزمایشگاه": "نمونه‌گیری، برچسب و صف نمونه‌گیری",
    "پذیرش آزمایشگاه": "پذیرش، دریافت وجه، نوبت و صف، تحویل جواب",
    "کارشناس بیمه آزمایشگاه": "قرارداد، صورت‌حساب و تسویه بیمه",
    "مشاور آزمایشگاه": "درخواست‌های مشاوره و نظرسنجی؛ بدون دسترسی به جواب‌ها",
    "کیوسک نوبت": "فقط صدور نوبت (برای دستگاه کیوسک)",
}
