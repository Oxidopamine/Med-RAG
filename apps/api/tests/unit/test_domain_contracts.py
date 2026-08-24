from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.domain import (
    ClinicalContext,
    EligibilityRule,
    Measurement,
    SourceStatus,
    SourceVersion,
)


def test_context_rejects_contradictory_condition_state() -> None:
    with pytest.raises(ValidationError, match="both present and absent"):
        ClinicalContext(
            conditions={"ATRIAL_FIBRILLATION"},
            known_absent_conditions={"ATRIAL_FIBRILLATION"},
        )


def test_context_rejects_duplicate_measurement_concepts() -> None:
    with pytest.raises(ValidationError, match="duplicate measurement concepts"):
        ClinicalContext(
            measurements=[
                Measurement(concept="CRCL", value=30, unit="mL/min"),
                Measurement(concept="CRCL", value=35, unit="mL/min"),
            ]
        )


def test_rule_rejects_condition_that_is_required_and_excluded() -> None:
    with pytest.raises(ValidationError, match="both required and excluded"):
        EligibilityRule(
            eligibility_rule_id="EL_CONTRADICTORY",
            required_conditions={"PREGNANCY"},
            excluded_conditions={"PREGNANCY"},
        )


def test_source_version_rejects_reversed_effective_range() -> None:
    with pytest.raises(ValidationError, match="effective_from cannot be after effective_to"):
        SourceVersion(
            source_version_id="SV_001",
            source_id="SRC_001",
            version_label="1",
            status=SourceStatus.EFFECTIVE,
            effective_from=date(2026, 2, 1),
            effective_to=date(2026, 1, 1),
        )


def test_canonical_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ClinicalContext.model_validate({"age": 50, "invented_fact": True})
