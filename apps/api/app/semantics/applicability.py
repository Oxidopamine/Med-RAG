import operator
from collections.abc import Callable

from app.schemas.domain import (
    ApplicabilityResult,
    ApplicabilityStatus,
    ClinicalContext,
    EligibilityRule,
    Materiality,
)

COMPARATORS: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    "=": operator.eq,
    "==": operator.eq,
    ">=": operator.ge,
    ">": operator.gt,
}

RELATED_BUT_NOT_INTERCHANGEABLE = {
    "CRCL": {"EGFR"},
    "EGFR": {"CRCL"},
}


def evaluate_applicability(
    rule: EligibilityRule,
    context: ClinicalContext,
) -> ApplicabilityResult:
    matched: list[str] = []
    unknown: list[str] = []
    mismatched: list[str] = []

    if rule.age:
        if context.age is None:
            unknown.append("age not provided")
        else:
            if rule.age.minimum is not None and context.age < rule.age.minimum:
                mismatched.append(f"age below required minimum {rule.age.minimum}")
            elif rule.age.maximum is not None and context.age > rule.age.maximum:
                mismatched.append(f"age above allowed maximum {rule.age.maximum}")
            else:
                matched.append("age")

    for condition in sorted(rule.required_conditions):
        if condition in context.conditions:
            matched.append(f"condition: {condition}")
        elif condition in context.known_absent_conditions:
            mismatched.append(f"required condition absent: {condition}")
        else:
            unknown.append(f"required condition not established: {condition}")

    for condition in sorted(rule.excluded_conditions):
        if condition in context.conditions:
            mismatched.append(f"excluded condition present: {condition}")
        elif condition in context.known_absent_conditions:
            matched.append(f"excluded condition absent: {condition}")
        else:
            unknown.append(f"excluded condition not assessed: {condition}")

    if rule.pregnancy_allowed is False:
        if "PREGNANCY" in context.special_populations:
            mismatched.append("pregnancy is excluded")
        elif "PREGNANCY" in context.known_absent_special_populations:
            matched.append("pregnancy excluded and known absent")
        else:
            unknown.append("pregnancy status not established")

    if rule.allowed_settings:
        if context.care_setting is None:
            unknown.append("care setting not provided")
        elif context.care_setting in rule.allowed_settings:
            matched.append(f"care setting: {context.care_setting}")
        else:
            mismatched.append(f"care setting not allowed: {context.care_setting}")

    measurements = {item.concept: item for item in context.measurements}
    for criterion in rule.measurements:
        actual = measurements.get(criterion.concept)
        if actual is None:
            related = RELATED_BUT_NOT_INTERCHANGEABLE.get(criterion.concept, set())
            provided_related = sorted(related & measurements.keys())
            if provided_related:
                unknown.append(
                    f"{criterion.concept} required; {', '.join(provided_related)} "
                    "is not interchangeable"
                )
            else:
                unknown.append(f"{criterion.concept} not provided")
            continue
        if actual.unit != criterion.unit:
            unknown.append(
                f"{criterion.concept} unit mismatch: expected {criterion.unit}, "
                f"received {actual.unit}"
            )
            continue
        comparator = COMPARATORS.get(criterion.operator)
        if comparator is None:
            unknown.append(f"unsupported operator: {criterion.operator}")
        elif comparator(actual.value, criterion.value):
            matched.append(f"measurement: {criterion.concept}")
        else:
            mismatched.append(
                f"{criterion.concept} does not satisfy {criterion.operator} {criterion.value:g}"
            )

    if mismatched:
        status = ApplicabilityStatus.MISMATCH
    elif unknown:
        status = ApplicabilityStatus.UNKNOWN
    else:
        status = ApplicabilityStatus.MATCH

    unconditional = status is ApplicabilityStatus.MATCH
    if rule.materiality is Materiality.HIGH and status is not ApplicabilityStatus.MATCH:
        unconditional = False

    return ApplicabilityResult(
        status=status,
        matched=matched,
        unknown=unknown,
        mismatched=mismatched,
        materiality=rule.materiality,
        eligible_for_unconditional_render=unconditional,
    )
