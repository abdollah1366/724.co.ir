from django import template

from apps.sales import cashbox

register = template.Library()


@register.inclusion_tag("includes/cashbox_status.html", takes_context=True)
def cashbox_status(context, department):
    """نوار وضعیت شیفت صندوق کاربر برای صفحه‌های دریافت وجه (درمانگاه، آزمایشگاه)."""
    user = context["request"].user
    session = cashbox.user_session(user, department)
    return {"session": session, "required": session is None and cashbox.department_has_registers(department),
            "label": cashbox.Department(department).label}
