from django.urls import path

from . import views

app_name = "appointments"

urlpatterns = [
    path("book/<str:slug>/", views.book, name="book"),
    path("track/", views.track, name="track"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/cancel/", views.cancel, name="cancel"),
]
