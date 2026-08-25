import json
from pathlib import Path
from typing import Any

import pytest

from app.schemas.domain import ClinicalContext, EligibilityRule
from app.semantics.applicability import evaluate_applicability

FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "data"
    / "fixtures"
    / "foundation_applicability.json"
)
FIXTURE_DOCUMENT = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
FIXTURE_CASES = FIXTURE_DOCUMENT["cases"]


def test_fixture_document_has_unique_case_ids() -> None:
    assert FIXTURE_DOCUMENT["schema_version"] == 1
    case_ids = [case["case_id"] for case in FIXTURE_CASES]
    assert case_ids
    assert len(case_ids) == len(set(case_ids))


@pytest.mark.parametrize(
    "case",
    FIXTURE_CASES,
    ids=[case["case_id"] for case in FIXTURE_CASES],
)
def test_foundation_applicability_fixture(case: dict[str, Any]) -> None:
    rule = EligibilityRule.model_validate(case["rule"])
    context = ClinicalContext.model_validate(case["context"])

    result = evaluate_applicability(rule, context)
    expected = case["expected"]

    assert result.status.value == expected["status"]
    assert result.unknown == expected["unknown"]
    assert result.mismatched == expected["mismatched"]
    assert (
        result.eligible_for_unconditional_render
        is expected["eligible_for_unconditional_render"]
    )
