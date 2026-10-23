"""ساخت برگه جواب: فقط جواب‌های تأییدشده، گروه‌بندی بر اساس بخش."""

from django.db.models import Q

from ..models import LabResult


def build_report(order, online=False):
    """online=True: آزمایش‌های حساس نمایش داده نمی‌شوند و فقط شمارش آن‌ها برمی‌گردد."""
    results = (
        LabResult.objects.filter(item__order=order)
        .select_related("item__test__section", "approved_by").prefetch_related("revisions")
        .order_by("item__test__section__order", "item__test__order")
    )
    sections, pending, hidden = [], 0, 0
    current = None
    for result in results:
        test = result.item.test
        if result.status != LabResult.Status.APPROVED:
            pending += 1
            continue
        if online and (test.is_sensitive or order.delivery == order.Delivery.IN_PERSON):
            hidden += 1
            continue
        if current is None or current["section"] != test.section:
            current = {"section": test.section, "results": []}
            sections.append(current)
        current["results"].append(result)
    approvers = {r.approved_by for s in sections for r in s["results"] if r.approved_by}
    last_approval = max((r.approved_at for s in sections for r in s["results"]), default=None)
    return {
        "order": order, "sections": sections, "pending": pending, "hidden": hidden, "approvers": approvers,
        "last_approval": last_approval, "is_complete": pending == 0,
    }


def trend(patient, test):
    """جواب‌های عددی تأییدشده یک آزمایش برای نمودار روند (قدیمی به جدید)."""
    return list(
        LabResult.objects.filter(
            item__order__patient=patient, item__test=test, status=LabResult.Status.APPROVED, numeric_value__isnull=False,
            item__test__is_sensitive=False,
        ).filter(~Q(item__order__status="reversed")).select_related("item__order").order_by("item__order__date", "approved_at")
    )


def trend_svg(points, width=560, height=180, low=None, high=None):
    """نمودار خطی ساده SVG: points = [(برچسب، مقدار)]. محدوده طبیعی به صورت نوار سبز."""
    if not points:
        return ""
    values = [float(v) for _, v in points] + [float(x) for x in (low, high) if x is not None]
    vmin, vmax = min(values), max(values)
    if vmax == vmin:
        vmax, vmin = vmax + 1, vmin - 1
    pad = (vmax - vmin) * 0.15
    vmin, vmax = vmin - pad, vmax + pad
    left, right, top, bottom = 48, 16, 12, 32

    def x(i):
        return left + (width - left - right) * (i / max(len(points) - 1, 1))

    def y(v):
        return top + (height - top - bottom) * (1 - (float(v) - vmin) / (vmax - vmin))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="trend" role="img" aria-label="نمودار روند">']
    if low is not None or high is not None:
        y_top = y(high) if high is not None else top
        y_bottom = y(low) if low is not None else height - bottom
        parts.append(f'<rect x="{left}" y="{y_top:.1f}" width="{width - left - right}" height="{max(y_bottom - y_top, 0):.1f}" fill="#ccfbf1"/>')
    for v in (vmin + pad, vmax - pad):
        parts.append(f'<text x="{left - 6}" y="{y(v) + 4:.1f}" font-size="11" text-anchor="end" fill="#6b7280">{v:g}</text>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(points))
    parts.append(f'<path d="{path}" fill="none" stroke="#0f766e" stroke-width="2"/>')
    for i, (label, v) in enumerate(points):
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" fill="#0f766e"><title>{label}: {float(v):g}</title></circle>')
        parts.append(f'<text x="{x(i):.1f}" y="{height - 10}" font-size="11" text-anchor="middle" fill="#6b7280">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)
