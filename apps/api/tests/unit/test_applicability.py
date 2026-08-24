from app.schemas.domain import (
    AgeCriterion,
    ApplicabilityStatus,
    ClinicalContext,
    EligibilityRule,
    Measurement,
    MeasurementCriterion,
)
from app.semantics.applicability import evaluate_applicability


def test_egfr_is_not_treated_as_creatinine_clearance() -> None:
    rule = EligibilityRule(
        eligibility_rule_id="EL_001",
        age=AgeCriterion(minimum=18),
        required_conditions={"ATRIAL_FIBRILLATION"},
        measurements=[
            MeasurementCriterion(
                concept="CRCL",
                operator=">=",
                value=30,
                unit="mL/min",
            )
        ],
    )
    context = ClinicalContext(
        age=74,
        conditions={"ATRIAL_FIBRILLATION"},
        measurements=[
            Measurement(concept="EGFR", value=28, unit="mL/min/1.73m2")
        ],
    )

    result = evaluate_applicability(rule, context)

    assert result.status is ApplicabilityStatus.UNKNOWN
    assert result.eligible_for_unconditional_render is False
    assert result.unknown == ["CRCL required; EGFR is not interchangeable"]


def test_matching_context_is_eligible() -> None:
    rule = EligibilityRule(
        eligibility_rule_id="EL_002",
        required_conditions={"ATRIAL_FIBRILLATION"},
        excluded_conditions={"MECHANICAL_HEART_VALVE"},
        measurements=[
            MeasurementCriterion(
                concept="CRCL",
                operator=">=",
                value=30,
                unit="mL/min",
            )
        ],
    )
    context = ClinicalContext(
        conditions={"ATRIAL_FIBRILLATION"},
        known_absent_conditions={"MECHANICAL_HEART_VALVE"},
        measurements=[Measurement(concept="CRCL", value=42, unit="mL/min")],
    )

    result = evaluate_applicability(rule, context)

    assert result.status is ApplicabilityStatus.MATCH
    assert result.eligible_for_unconditional_render is True


def test_excluded_condition_forces_mismatch() -> None:
    rule = EligibilityRule(
        eligibility_rule_id="EL_003",
        required_conditions={"ATRIAL_FIBRILLATION"},
        excluded_conditions={"MECHANICAL_HEART_VALVE"},
    )
    context = ClinicalContext(
        conditions={"ATRIAL_FIBRILLATION", "MECHANICAL_HEART_VALVE"}
    )

    result = evaluate_applicability(rule, context)

    assert result.status is ApplicabilityStatus.MISMATCH
    assert "excluded condition present: MECHANICAL_HEART_VALVE" in result.mismatched


def test_unassessed_exclusion_is_unknown_not_implicitly_absent() -> None:
    rule = EligibilityRule(
        eligibility_rule_id="EL_004",
        required_conditions={"ATRIAL_FIBRILLATION"},
        excluded_conditions={"MECHANICAL_HEART_VALVE"},
    )
    context = ClinicalContext(conditions={"ATRIAL_FIBRILLATION"})

    result = evaluate_applicability(rule, context)

    assert result.status is ApplicabilityStatus.UNKNOWN
    assert result.eligible_for_unconditional_render is False
    assert result.unknown == [
        "excluded condition not assessed: MECHANICAL_HEART_VALVE"
    ]


def test_setting_and_pregnancy_restrictions_fail_closed_when_unknown() -> None:
    rule = EligibilityRule(
        eligibility_rule_id="EL_005",
        pregnancy_allowed=False,
        allowed_settings={"OUTPATIENT"},
    )

    result = evaluate_applicability(rule, ClinicalContext())

    assert result.status is ApplicabilityStatus.UNKNOWN
    assert result.unknown == [
        "pregnancy status not established",
        "care setting not provided",
    ]
