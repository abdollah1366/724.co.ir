"""ثبت تغییرات مدل‌های حساس آزمایشگاه در گزارش تغییرات."""

from apps.audit import log

from .models import (
    InsurancePlan, Instrument, LabPatient, LabResult, LabTest, PlanTestRule, QCMaterial, ReferenceRange, SamplingAppointment,
    Specimen,
)

for model in (
    LabPatient, InsurancePlan, PlanTestRule, LabTest, ReferenceRange, LabResult, Specimen, QCMaterial, Instrument,
    SamplingAppointment,
):
    log.track(model)
