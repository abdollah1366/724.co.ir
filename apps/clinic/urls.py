from django.urls import path

from . import views

app_name = "clinic"

urlpatterns = [
    path("floors/<str:slug>/", views.floor_detail, name="floor_detail"),
    path("doctors/", views.doctor_list, name="doctor_list"),
    path("doctors/<str:slug>/", views.doctor_detail, name="doctor_detail"),
    path("search/", views.search, name="search"),
]
