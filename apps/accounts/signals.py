from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.appointments.models import Appointment
from apps.shop.models import Order

from . import club


@receiver(post_save, sender=Appointment)
def appointment_points(sender, instance, **kwargs):
    if instance.status != Appointment.Status.DONE:
        return
    # ژتون فقط به حسابی داده می‌شود که نوبت را ثبت کرده (موبایل هنوز با پیامک تأیید نمی‌شود).
    club.service_completed(
        instance.user, club.Reason.APPOINTMENT, club.rule("appointment_done"), f"appointment:{instance.pk}",
        f"ویزیت {instance.doctor}",
    )


@receiver(post_save, sender=Order)
def order_points(sender, instance, **kwargs):
    if instance.status != Order.Status.DELIVERED:
        return
    club.service_completed(
        instance.user, club.Reason.ORDER, club.order_points(instance.total), f"order:{instance.pk}",
        f"سفارش {instance.code}",
    )
