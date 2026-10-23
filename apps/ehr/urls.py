from django.urls import path

from . import views

app_name = "ehr"

urlpatterns = [
    path("", views.patient_search, name="search"),
    path("my-patients/", views.my_patients, name="my_patients"),
    path("patients/new/", views.patient_edit, name="patient_new"),
    path("patients/<int:pk>/", views.chart, name="chart"),
    path("patients/<int:pk>/edit/", views.patient_edit, name="patient_edit"),
    path("access-log/", views.access_log, name="access_log"),
]
