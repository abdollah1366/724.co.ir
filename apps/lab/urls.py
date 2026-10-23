from django.urls import path

from .views import api, booking, dashboard, finance, lis, portal, public, staff

app_name = "lab"

urlpatterns = [
    # عمومی
    path("", public.home, name="home"),
    path("tests/", public.test_list, name="test_list"),
    path("tests/<str:slug>/", public.test_detail, name="test_detail"),
    path("panels/<str:slug>/", public.panel_detail, name="panel_detail"),
    path("preparation/", public.preparation, name="preparation"),
    path("checkup-guide/", public.screening_guide, name="screening_guide"),
    path("book/", booking.book, name="book"),
    path("book/track/", booking.booking_track, name="booking_track"),
    path("book/<int:pk>/", booking.booking_detail, name="booking_detail"),
    path("book/<int:pk>/cancel/", booking.booking_cancel, name="booking_cancel"),
    # جواب آنلاین
    path("results/", portal.result_lookup, name="result_lookup"),
    path("results/code/", portal.result_verify, name="result_verify"),
    path("results/<int:pk>/", portal.result_view, name="result_view"),
    path("my/", portal.my_results, name="my_results"),
    path("my/link/", portal.link_patient, name="link_patient"),
    path("my/trend/<str:code>/", portal.my_trend, name="my_trend"),
    path("verify/", portal.verify_report, name="verify_report"),
    # API دستگاه
    path("api/instrument/results/", api.instrument_results, name="api_instrument_results"),
    # پیشخوان
    path("staff/", dashboard.dashboard, name="staff_dashboard"),
    path("staff/reception/", staff.reception, name="staff_reception"),
    path("staff/reception/<int:pk>/", staff.reception, name="staff_reception_edit"),
    path("staff/orders/", staff.order_list, name="staff_orders"),
    path("staff/orders/<int:pk>/", staff.order_detail, name="staff_order"),
    path("staff/orders/<int:pk>/receipt/", staff.receipt, name="staff_receipt"),
    path("staff/orders/<int:pk>/labels/", lis.labels, name="staff_labels"),
    path("staff/orders/<int:pk>/results/", lis.results, name="staff_results"),
    path("staff/orders/<int:pk>/report/", lis.staff_report, name="staff_report"),
    path("staff/orders/<int:pk>/<str:action>/", staff.order_action, name="staff_order_action"),
    path("staff/payments/<int:pk>/reverse/", finance.payment_reverse, name="staff_payment_reverse"),
    path("staff/appointments/", staff.appointments, name="staff_appointments"),
    path("staff/appointments/<int:pk>/status/", staff.appointment_status, name="staff_appointment_status"),
    path("staff/specimens/<int:pk>/<str:action>/", lis.specimen_action, name="staff_specimen"),
    path("staff/worklist/", lis.worklist, name="staff_worklist"),
    path("staff/results/<int:pk>/amend/", lis.amend, name="staff_amend"),
    path("staff/results/<int:pk>/critical/", lis.critical_notify, name="staff_critical"),
    path("staff/qc/", lis.qc, name="staff_qc"),
    path("staff/qc/<int:pk>/", lis.qc_chart, name="staff_qc_chart"),
    path("staff/claims/", finance.claims, name="staff_claims"),
    path("staff/claims/<int:pk>/", finance.claim_detail, name="staff_claim"),
    path("staff/cashier/", finance.cashier_report, name="staff_cashier"),
]
