"""محاسبه تعرفه و سهم بیمه پایه، بیمه تکمیلی و بیمار برای هر آزمایش پذیرش.

قواعد:
- بدون بیمه پایه: تعرفه آزاد؛ قیمت بسته (پنل) به نسبت تعرفه آزاد بین آزمایش‌های آن تقسیم می‌شود.
- با بیمه پایه: تعرفه قرارداد = تعرفه توافقی آزمایش یا درصدی از تعرفه آزاد؛ بیمه درصد پوشش را از آن می‌پردازد.
- بیمه تکمیلی درصدی از سهم باقی‌مانده بیمار را می‌پردازد، تا سقف تعهد هر پذیرش.
- همه مبالغ ریال صحیح است؛ سهم بیمه به پایین گرد می‌شود و جمع ردیف‌ها با جمع کل برابر است.
"""

from dataclasses import dataclass


@dataclass
class PricedLine:
    test: object
    panel: object = None
    price: int = 0
    basic_share: int = 0
    supplementary_share: int = 0
    patient_share: int = 0
    basic_covered: bool = False
    needs_approval: bool = False


def expand_selection(tests=(), panels=()):
    """آزمایش‌های تکی و بسته‌ها را به فهرست بدون تکرار (آزمایش، بسته) تبدیل می‌کند. انتخاب تکی بر بسته مقدم است."""
    chosen = {}
    for test in tests:
        chosen[test.pk] = (test, None)
    for panel in panels:
        for test in panel.tests.filter(is_active=True):
            chosen.setdefault(test.pk, (test, panel))
    return list(chosen.values())


def _distribute(total, weights):
    """تقسیم total به نسبت weights؛ باقی‌مانده گردکردن به آخرین سهم می‌رسد."""
    weight_sum = sum(weights)
    if not weights:
        return []
    if weight_sum == 0:
        shares = [0] * len(weights)
        shares[-1] = total
        return shares
    shares = [total * w // weight_sum for w in weights]
    shares[-1] += total - sum(shares)
    return shares


def _rules(plan):
    return {r.test_id: r for r in plan.rules.all()} if plan else {}


def price_lines(selection, basic_plan=None, supplementary_plan=None):
    lines = [PricedLine(test=test, panel=panel) for test, panel in selection]
    basic_rules = _rules(basic_plan)
    if basic_plan:
        for line in lines:
            rule = basic_rules.get(line.test.pk)
            if rule and rule.price is not None:
                line.price = rule.price
            else:
                line.price = line.test.price * basic_plan.tariff_percent // 100
            line.basic_covered = rule.is_covered if rule else True
            if line.basic_covered:
                percent = rule.coverage_percent if rule and rule.coverage_percent is not None else basic_plan.coverage_percent
                line.basic_share = line.price * percent // 100
            line.needs_approval = bool(rule and rule.requires_approval)
    else:
        for line in lines:
            if line.panel is None or line.panel.package_price is None:
                line.price = line.test.price
        by_panel = {}
        for line in lines:
            if line.panel is not None and line.panel.package_price is not None:
                by_panel.setdefault(line.panel.pk, []).append(line)
        for group in by_panel.values():
            panel = group[0].panel
            # اگر بخشی از آزمایش‌های بسته جداگانه انتخاب شده، قیمت بسته به نسبت آزمایش‌های باقی‌مانده کم می‌شود.
            all_tests = list(panel.tests.filter(is_active=True))
            full = sum(t.price for t in all_tests) or 1
            included = sum(line.test.price for line in group)
            package = panel.package_price * included // full if len(group) < len(all_tests) else panel.package_price
            for line, share in zip(group, _distribute(package, [line.test.price for line in group])):
                line.price = share
    for line in lines:
        line.patient_share = line.price - line.basic_share
    if supplementary_plan:
        supp_rules = _rules(supplementary_plan)
        for line in lines:
            rule = supp_rules.get(line.test.pk)
            if rule and not rule.is_covered:
                continue
            percent = rule.coverage_percent if rule and rule.coverage_percent is not None else supplementary_plan.coverage_percent
            line.supplementary_share = line.patient_share * percent // 100
            line.needs_approval = line.needs_approval or bool(rule and rule.requires_approval)
        cap = supplementary_plan.max_per_order
        total = sum(line.supplementary_share for line in lines)
        if cap is not None and total > cap:
            scaled = _distribute(cap, [line.supplementary_share for line in lines])
            for line, share in zip(lines, scaled):
                line.supplementary_share = share
        for line in lines:
            line.patient_share -= line.supplementary_share
    return lines


def home_fee_split(fee, supplementary_plan, supplementary_used):
    """سهم بیمه تکمیلی و بیمار از هزینه نمونه‌گیری در منزل."""
    if not fee or not supplementary_plan or not supplementary_plan.covers_home_sampling:
        return 0, fee
    covered = fee * supplementary_plan.coverage_percent // 100
    if supplementary_plan.max_per_order is not None:
        covered = max(min(covered, supplementary_plan.max_per_order - supplementary_used), 0)
    return covered, fee - covered


def totals(lines, home_fee=0, supplementary_plan=None):
    gross = sum(line.price for line in lines) + home_fee
    basic = sum(line.basic_share for line in lines)
    supp = sum(line.supplementary_share for line in lines)
    home_supp, home_patient = home_fee_split(home_fee, supplementary_plan, supp)
    patient = sum(line.patient_share for line in lines) + home_patient
    return {"gross": gross, "basic": basic, "supplementary": supp + home_supp, "patient": patient}
