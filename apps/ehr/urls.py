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
    path("patients/<int:pk>/encounters/start/", views.encounter_start, name="encounter_start"),
    path("patients/<int:pk>/add/<str:what>/", views.chart_add, name="chart_add"),
    path("encounters/<int:pk>/", views.encounter, name="encounter"),
    path("encounters/<int:pk>/vitals/", views.encounter_vitals, name="encounter_vitals"),
    path("encounters/<int:pk>/diagnosis/", views.encounter_diagnosis, name="encounter_diagnosis"),
    path("encounters/<int:pk>/referral/", views.encounter_referral, name="encounter_referral"),
    path("encounters/<int:pk>/sign/", views.encounter_sign, name="encounter_sign"),
    path("encounters/<int:pk>/addendum/", views.encounter_addendum, name="encounter_addendum"),
    path("diagnoses/<int:pk>/delete/", views.diagnosis_delete, name="diagnosis_delete"),
    path("problems/<int:pk>/toggle/", views.problem_resolve, name="problem_toggle"),
    path("allergies/<int:pk>/toggle/", views.allergy_toggle, name="allergy_toggle"),
    path("icd10/", views.icd_search, name="icd_search"),
    path("registry/", views.chronic_registry, name="registry"),
]
