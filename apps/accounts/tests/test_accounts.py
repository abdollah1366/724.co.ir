import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PointTransaction, Profile
from apps.appointments.models import Appointment
from apps.clinic.tests.factories import make_doctor
from apps.shop.models import Order

PASSWORD = "Saf3-Passw0rd-724"


def signup(client, mobile="09121234567", ref=""):
    return client.post(
        reverse("accounts:signup"),
        {
            "first_name": "مریم", "last_name": "رضایی", "mobile": mobile, "password1": PASSWORD,
            "password2": PASSWORD, "referral_code": ref, "consent": "on",
        },
    )


def make_appointment(user=None, mobile="09121234567", doctor=None, time=datetime.time(9)):
    return Appointment.objects.create(
        doctor=doctor or make_doctor(), user=user, date=timezone.localdate() + datetime.timedelta(days=1),
        time=time, patient_full_name="مریم", national_code="0499370899", mobile=mobile,
        consent_at=timezone.now(),
    )


class SignupLoginTests(TestCase):
    def test_signup_creates_user_and_profile(self):
        response = signup(self.client, mobile="۰۹۱۲۱۲۳۴۵۶۷")
        self.assertRedirects(response, reverse("accounts:dashboard"))
        profile = Profile.objects.get()
        self.assertEqual(profile.mobile, "09121234567")
        self.assertEqual(profile.user.username, "09121234567")
        self.assertEqual(len(profile.referral_code), 6)

    def test_duplicate_mobile_rejected(self):
        signup(self.client)
        self.client.logout()
        signup(self.client)
        self.assertEqual(Profile.objects.count(), 1)

    def test_weak_password_rejected(self):
        response = self.client.post(
            reverse("accounts:signup"),
            {"first_name": "a", "last_name": "b", "mobile": "09121234567", "password1": "123456",
             "password2": "123456", "consent": "on"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Profile.objects.exists())

    def test_invalid_referral_code_rejected(self):
        signup(self.client, ref="NOPE99")
        self.assertFalse(Profile.objects.exists())

    def test_login_with_mobile(self):
        signup(self.client)
        self.client.logout()
        response = self.client.post(reverse("accounts:login"), {"username": "۰۹۱۲۱۲۳۴۵۶۷", "password": PASSWORD})
        self.assertRedirects(response, reverse("accounts:dashboard"))

    def test_dashboard_requires_login(self):
        self.assertEqual(self.client.get(reverse("accounts:dashboard")).status_code, 302)

    def test_dashboard_works_for_superuser_without_profile(self):
        admin = get_user_model().objects.create_superuser("admin", password=PASSWORD)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("accounts:dashboard")).status_code, 200)

    def test_open_redirect_blocked(self):
        response = self.client.post(
            reverse("accounts:signup") + "?next=https://evil.example/",
            {"first_name": "م", "last_name": "ر", "mobile": "09121234567", "password1": PASSWORD,
             "password2": PASSWORD, "consent": "on"},
        )
        self.assertRedirects(response, reverse("accounts:dashboard"))


class ClubPointsTests(TestCase):
    def setUp(self):
        signup(self.client)
        self.user = Profile.objects.get(mobile="09121234567").user

    def points(self, user):
        return Profile.objects.get(user=user).points

    def test_booking_while_logged_in_links_user(self):
        doctor = make_doctor()
        day = timezone.localdate() + datetime.timedelta(days=1)
        self.client.post(
            reverse("appointments:book", args=[doctor.slug]),
            {"date": day.isoformat(), "time": "09:00", "patient_full_name": "مریم", "national_code": "0499370899",
             "mobile": "09121234567", "consent": "on"},
        )
        self.assertEqual(Appointment.objects.get().user, self.user)

    def test_points_only_when_appointment_done_and_only_once(self):
        appointment = make_appointment(user=self.user)
        self.assertEqual(self.points(self.user), 0)
        appointment.status = Appointment.Status.DONE
        appointment.save()
        appointment.save()
        self.assertEqual(self.points(self.user), 50)

    def test_guest_appointment_not_matched_by_unverified_mobile(self):
        # تا تأیید پیامکی موبایل، نوبت مهمان به حسابی که همان شماره را دارد وصل نمی‌شود.
        appointment = make_appointment(user=None, mobile="09121234567")
        appointment.status = Appointment.Status.DONE
        appointment.save()
        self.assertEqual(self.points(self.user), 0)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertNotContains(response, appointment.doctor.full_name)

    def test_order_points_on_delivery(self):
        order = Order.objects.create(user=self.user, full_name="م", mobile="09121234567", total=2_500_000)
        self.assertEqual(self.points(self.user), 0)
        order.status = Order.Status.DELIVERED
        order.save()
        self.assertEqual(self.points(self.user), 25)

    def test_referral_rewarded_after_friends_first_service(self):
        referrer = Profile.objects.get(user=self.user)
        self.client.logout()
        signup(self.client, mobile="09350000000", ref=referrer.referral_code.lower())
        friend = Profile.objects.get(mobile="09350000000")
        self.assertEqual(friend.referred_by, referrer)
        self.assertEqual(self.points(self.user), 0)  # عضویت به‌تنهایی ژتون نمی‌دهد

        doctor = make_doctor()
        for time in (datetime.time(9), datetime.time(9, 30)):
            appointment = make_appointment(user=friend.user, mobile="09350000000", doctor=doctor, time=time)
            appointment.status = Appointment.Status.DONE
            appointment.save()
        self.assertEqual(self.points(self.user), 100)
        self.assertEqual(self.points(friend.user), 50 + 50 + 50)
        self.assertEqual(PointTransaction.objects.filter(reason="referral").count(), 1)

    def test_dashboard_shows_points_and_referral_link(self):
        PointTransaction.objects.create(user=self.user, amount=30, reason="adjust")
        response = self.client.get(reverse("accounts:dashboard"))
        profile = Profile.objects.get(user=self.user)
        self.assertContains(response, "۳۰")
        self.assertContains(response, f"?ref={profile.referral_code}")

    def test_club_page(self):
        response = self.client.get(reverse("club"))
        self.assertContains(response, "معرفی به دوستان")


class RewardsAndLevelsTests(TestCase):
    def setUp(self):
        from apps.accounts.models import Reward

        signup(self.client)
        self.user = Profile.objects.get(mobile="09121234567").user
        self.reward = Reward.objects.create(title="نوشیدنی رایگان", cost=100)

    def give(self, amount, ref):
        PointTransaction.objects.create(user=self.user, amount=amount, reason="appointment", reference=ref)

    def test_redeem_deducts_points_and_issues_code(self):
        from apps.accounts.models import Redemption

        self.give(150, "a1")
        self.client.post(reverse("accounts:redeem", args=[self.reward.pk]))
        redemption = Redemption.objects.get()
        self.assertEqual(redemption.status, "issued")
        self.assertTrue(redemption.code.startswith("R"))
        self.assertEqual(Profile.objects.get(user=self.user).points, 50)
        self.assertContains(self.client.get(reverse("accounts:dashboard")), redemption.code)

    def test_cannot_redeem_without_enough_points(self):
        from apps.accounts.models import Redemption

        self.give(99, "a1")
        self.client.post(reverse("accounts:redeem", args=[self.reward.pk]))
        self.assertFalse(Redemption.objects.exists())
        self.assertEqual(Profile.objects.get(user=self.user).points, 99)

    def test_redeem_requires_post_and_login(self):
        self.assertEqual(self.client.get(reverse("accounts:redeem", args=[self.reward.pk])).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("accounts:redeem", args=[self.reward.pk])).status_code, 302)

    def test_cancel_refunds_once_and_keeps_level(self):
        from apps.accounts import club
        from apps.accounts.models import Redemption

        self.give(600, "a1")
        self.client.post(reverse("accounts:redeem", args=[self.reward.pk]))
        redemption = Redemption.objects.get()
        self.assertTrue(club.cancel_redemption(redemption))
        self.assertFalse(club.cancel_redemption(redemption))
        self.assertEqual(Profile.objects.get(user=self.user).points, 600)
        # ژتون برگشتی سطح را بالا نمی‌برد و خرج کردن ژتون سطح را پایین نمی‌آورد
        self.assertEqual(club.level_for(self.user)["earned"], 600)

    def test_levels(self):
        from apps.accounts import club

        self.assertEqual(club.level_for(self.user)["name"], "برنزی")
        self.give(500, "a1")
        level = club.level_for(self.user)
        self.assertEqual(level["name"], "نقره‌ای")
        self.assertEqual(level["to_next"], 1000)
        self.give(1000, "a2")
        self.assertEqual(club.level_for(self.user)["next_name"], None)
        self.assertContains(self.client.get(reverse("accounts:dashboard")), "طلایی")
