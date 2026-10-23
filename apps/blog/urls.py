from django.urls import path

from . import views
from .feeds import LatestPostsFeed

app_name = "blog"

urlpatterns = [
    path("", views.post_list, name="list"),
    path("feed/", LatestPostsFeed(), name="feed"),
    path("manage/", views.manage, name="manage"),
    path("category/<str:slug>/", views.category, name="category"),
    path("tag/<str:slug>/", views.tag, name="tag"),
    path("<str:slug>/", views.detail, name="detail"),
]
