"""اتصال خودکار نوبت‌ها و بیماران آزمایشگاه به پرونده واحد با کد ملی."""

from django.db.models.signals import pre_save
from django.dispatch import receiver

from apps.appointments.models import Appointment
from apps.audit import log
from apps.lab.models import LabPatient

from . import services
from .models import Patient

log.track(Patient)


@receiver(pre_save, sender=LabPatient, dispatch_uid="ehr-link-lab-patient")
def _link_lab_patient(sender, instance, raw=False, **kwargs):
    if not raw and not instance.patient_id:
        instance.patient = services.link_lab_patient(instance)


@receiver(pre_save, sender=Appointment, dispatch_uid="ehr-link-appointment")
def _link_appointment(sender, instance, raw=False, **kwargs):
    if not raw and not instance.patient_id and instance.national_code:
        instance.patient = services.link_appointment(instance)
