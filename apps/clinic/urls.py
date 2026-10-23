from django.urls import path

from . import views

app_name = "clinic"

urlpatterns = [
    path("services/", views.department_list, name="department_list"),
    path("services/<str:slug>/", views.department_detail, name="department_detail"),
    path("doctors/", views.doctor_list, name="doctor_list"),
    path("doctors/<str:slug>/", views.doctor_detail, name="doctor_detail"),
    path("search/", views.search, name="search"),
]
