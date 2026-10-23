"""صفحات عمومی آزمایشگاه: فهرست و جزئیات آزمایش‌ها، آمادگی و راهنمای آزمایش‌های دوره‌ای."""

from django import forms
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.core.validators import normalize_digits

from ..models import LabPanel, LabSection, LabTest, PrepInstruction, RiskFactor, ScreeningRule, Sex


def _public_tests():
    return LabTest.objects.public().select_related("section", "specimen_type").prefetch_related("preparations")


def home(request):
    return render(request, "lab/home.html", {
        "sections": LabSection.objects.filter(is_active=True, tests__is_public=True, tests__is_active=True).distinct(),
        "panels": LabPanel.objects.filter(is_active=True, is_public=True).prefetch_related("tests"),
        "lab_links": [
            {"url": reverse("lab:book"), "icon": "📅", "title": "نوبت نمونه‌گیری"},
            {"url": reverse("lab:result_lookup"), "icon": "📄", "title": "دریافت جواب"},
            {"url": reverse("lab:consult"), "icon": "💬", "title": "مشاور آزمایشگاه"},
        ],
    })


def search_tests(query):
    query = normalize_digits(query)
    if not query:
        return LabTest.objects.none()
    return _public_tests().filter(
        Q(name__icontains=query) | Q(name_en__icontains=query) | Q(code__iexact=query) | Q(aliases__icontains=query)
    )


def test_list(request):
    query = request.GET.get("q", "").strip()
    section = request.GET.get("section", "")
    tests = search_tests(query) if query else _public_tests()
    if section:
        tests = tests.filter(section__code=section)
    return render(request, "lab/test_list.html", {
        "tests": tests, "query": query, "current_section": section,
        "sections": LabSection.objects.filter(is_active=True),
    })


def test_detail(request, slug):
    test = get_object_or_404(_public_tests(), slug=slug)
    return render(request, "lab/test_detail.html", {
        "test": test,
        "panels": test.panels.filter(is_active=True, is_public=True),
        "related": _public_tests().filter(section=test.section).exclude(pk=test.pk)[:6],
    })


def panel_detail(request, slug):
    panel = get_object_or_404(
        LabPanel.objects.filter(is_active=True, is_public=True).prefetch_related(
            Prefetch("tests", queryset=_public_tests())
        ),
        slug=slug,
    )
    return render(request, "lab/panel_detail.html", {"panel": panel, "prep": combined_preparation(panel.tests.all())})


def combined_preparation(tests):
    """آمادگی چند آزمایش با هم: بیشترین ساعت ناشتایی و فهرست بدون تکرار دستورها."""
    tests = list(tests)
    preps = PrepInstruction.objects.filter(tests__in=tests).distinct()
    fasting = max((p.fasting_hours or 0 for p in preps), default=0)
    specimens = sorted({t.specimen_type.name for t in tests})
    return {"tests": tests, "preps": preps, "fasting_hours": fasting or None, "specimens": specimens}


def preparation(request):
    """بیمار آزمایش‌های نسخه را انتخاب می‌کند و آمادگی یکجا را می‌بیند. چیزی ذخیره نمی‌شود."""
    codes = request.GET.getlist("t")
    selected = _public_tests().filter(code__in=codes) if codes else LabTest.objects.none()
    return render(request, "lab/preparation.html", {
        "all_tests": _public_tests(), "selected_codes": set(codes),
        "prep": combined_preparation(selected) if codes else None,
    })


class ScreeningForm(forms.Form):
    age = forms.IntegerField(label="سن (سال)", min_value=1, max_value=110)
    sex = forms.ChoiceField(label="جنس", choices=[(Sex.MALE, "مرد"), (Sex.FEMALE, "زن")], widget=forms.RadioSelect)
    risks = forms.MultipleChoiceField(
        label="عوامل خطر (اختیاری)", required=False, widget=forms.CheckboxSelectMultiple,
        choices=[c for c in RiskFactor.choices if c[0]],
    )

    def __init__(self, data=None, **kwargs):
        if data is not None:
            data = data.copy()
            data["age"] = normalize_digits(data.get("age", ""))
        super().__init__(data, **kwargs)


def screening_guide(request):
    """راهنمای آزمایش‌های دوره‌ای. پاسخ‌ها فقط در همین درخواست استفاده و هرگز ذخیره نمی‌شوند."""
    form = ScreeningForm(request.GET or None)
    rules = None
    if form.is_valid():
        data = form.cleaned_data
        rules = [
            r for r in ScreeningRule.objects.filter(is_approved=True).prefetch_related(
                Prefetch("tests", queryset=_public_tests())
            )
            if r.applies_to(data["age"], data["sex"], set(data["risks"]))
        ]
    response = render(request, "lab/screening_guide.html", {"form": form, "rules": rules})
    response["Cache-Control"] = "no-store"
    return response
