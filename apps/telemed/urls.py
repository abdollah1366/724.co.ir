from django.urls import path

from . import views

app_name = "telemed"

urlpatterns = [
    path("", views.index, name="index"),
    path("doctor/<str:slug>/", views.doctor, name="doctor"),
    path("doctor/<str:slug>/<str:kind>/new/", views.new_doctor, name="new_doctor"),
    path("pharmacy/new/", views.new_pharmacy, name="new_pharmacy"),
    path("mine/", views.mine, name="mine"),
    path("inbox/", views.inbox, name="inbox"),
    path("report/", views.report, name="report"),
    path("c/<str:code>/", views.detail, name="detail"),
    path("c/<str:code>/pay/", views.pay, name="pay"),
    path("c/<str:code>/cancel/", views.cancel, name="cancel"),
    path("c/<str:code>/close/", views.close, name="close"),
    path("c/<str:code>/decline/", views.decline, name="decline"),
    path("c/<str:code>/meeting-link/", views.meeting_link, name="meeting_link"),
    path("files/<int:pk>/", views.attachment, name="attachment"),
    path("pay/return/<str:authority>/", views.pay_callback, name="pay_callback"),
    path("pay/sandbox/<str:authority>/", views.sandbox_pay, name="sandbox_pay"),
]
