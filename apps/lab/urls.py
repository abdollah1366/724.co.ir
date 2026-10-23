from django.urls import path

from .views import public

app_name = "lab"

urlpatterns = [
    path("", public.home, name="home"),
    path("tests/", public.test_list, name="test_list"),
    path("tests/<str:slug>/", public.test_detail, name="test_detail"),
    path("panels/<str:slug>/", public.panel_detail, name="panel_detail"),
    path("preparation/", public.preparation, name="preparation"),
    path("checkup-guide/", public.screening_guide, name="screening_guide"),
]
