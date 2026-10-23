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
        },
        "extra": [
            "sales.approve_prescription", "sales.post_salesinvoice", "sales.post_salesreturn", "sales.use_pos",
            "inventory.change_batch_status", "inventory.post_inventorydocument",
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
        "models": LAB_RECEPTION,
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

EHR_CLINICAL = {"ehr.patient": (VIEW,), "appointments.appointment": (VIEW,), "lab.labtest": (VIEW,), "ehr.icd10code": (VIEW,)}
EHR_DOCTOR = {
    **EHR_CLINICAL, "ehr.encounter": ("add", "change", VIEW), "ehr.encounteraddendum": ("add", VIEW),
    "ehr.vitalsigns": ("add", VIEW), "ehr.diagnosis": EDIT, "ehr.problem": ("add", "change", VIEW),
    "ehr.allergy": ("add", "change", VIEW), "ehr.historyentry": ("add", "change", VIEW), "ehr.referral": ("add", "change", VIEW),
    "ehr.encounterservice": EDIT, "ehr.servicecode": (VIEW,), "ehr.genericdrug": (VIEW,),
}
EHR_NURSE = {
    **EHR_CLINICAL, "ehr.encounter": (VIEW,), "ehr.vitalsigns": ("add", VIEW), "ehr.diagnosis": (VIEW,),
    "ehr.problem": (VIEW,), "ehr.allergy": ("add", VIEW), "ehr.historyentry": ("add", VIEW), "ehr.referral": (VIEW,),
}

ROLES.update({
    "پزشک": {"models": EHR_DOCTOR, "extra": ["ehr.view_clinical_record", "ehr.sign_encounter"]},
    "پرستار": {"models": EHR_NURSE, "extra": ["ehr.view_clinical_record"]},
    "پذیرش درمانگاه": {
        "models": {"ehr.patient": ("add", "change", VIEW), "appointments.appointment": ("add", "change", VIEW)},
        "extra": [],
    },
    "مسئول فنی درمانگاه": {
        "models": {
            **EHR_DOCTOR, "ehr.patient": ("add", "change", VIEW), "ehr.recordaccess": (VIEW,), "audit.auditlog": (VIEW,),
            "clinic.doctor": ("change", VIEW), "ehr.icd10code": EDIT,
            "ehr.servicecode": EDIT, "ehr.rvucoefficient": EDIT, "ehr.genericdrug": EDIT,
        },
        "extra": ["ehr.view_clinical_record", "ehr.view_all_records"],
    },
})


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
