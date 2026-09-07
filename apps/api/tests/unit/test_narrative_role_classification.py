"""Evidence roles for narrative prose, decided from the passage rather than the asset.

The rule was scored against 165 GRADE-rated recommendations from the real WHO guideline
before it was written (docs/narrative-role-classification.md). These tests pin the
properties that measurement established, so a later tidy-up cannot quietly undo them.
"""

import pytest

from app.corpus_steward.qa_classification import (
    _base_roles,
    _narrative_applicability,
)
from app.schemas.corpus import EvidenceRole

NARRATIVE_UNIT = "pdf:page:12:block:3"
ASSET = "GUIDELINE_HYPERTENSION"

STRONG = (
    "Adults with confirmed hypertension and blood pressure at or above 140/90 mmHg "
    "should be started on pharmacological treatment and reviewed at three months."
)
CONDITIONAL = (
    "WHO suggests using an HPV DNA primary screening test with triage rather than "
    "without triage, among both the general population of women and women living with HIV."
)
METHODS = (
    "The guideline development group met in Geneva during 2021 and reviewed the evidence "
    "profiles prepared by the systematic review team over the course of four sessions."
)


def roles(text: str, unit: str = NARRATIVE_UNIT) -> tuple[set[EvidenceRole], str]:
    return _base_roles(ASSET, unit, text)


@pytest.mark.parametrize(("text", "register"), [(STRONG, "strong"), (CONDITIONAL, "conditional")])
def test_both_grade_registers_carry_primary_support(text: str, register: str) -> None:
    """"We recommend" is strong and "we suggest" is conditional.

    A vocabulary carrying only the first discards every conditional recommendation in the
    corpus - measured, that was 8 of 165 and the whole difference between 0.952 and 1.000.
    """

    assigned, rule = roles(text)

    assert EvidenceRole.PRIMARY_SUPPORT in assigned, register
    assert rule == "narrative-deontic-statement"


def test_prose_carrying_no_obligation_is_rationale_not_support() -> None:
    """Context can support a claim alongside a recommendation; it cannot be one."""

    assigned, rule = roles(METHODS)

    assert assigned == {EvidenceRole.RATIONALE}
    assert EvidenceRole.PRIMARY_SUPPORT not in assigned
    assert rule == "narrative-supporting-prose"


@pytest.mark.parametrize(
    "text",
    [
        "References",
        "1. Smith J, Jones A et al. Treatment outcomes. Geneva: WHO; 2019.",
        "Acknowledgements",
        "Annex 2: key drug interactions",
        "Short fragment.",
    ],
)
def test_structure_that_cannot_recommend_gets_no_role(text: str) -> None:
    """No roles means quarantine, which is the correct outcome for furniture."""

    assigned, _rule = roles(text)

    assert assigned == set()


def test_the_dak_path_is_untouched() -> None:
    """The narrative branch is selected by unit shape, so signed history cannot move."""

    assigned, rule = _base_roles("WHO_HIV_DAK_2_ANNEX_A", "xlsx:Annex A:row:5", "anything")

    assert assigned == {EvidenceRole.PRIMARY_SUPPORT}
    assert rule == "who-data-dictionary"


def test_applicability_is_read_from_the_passage_not_a_disease_vocabulary() -> None:
    """The DAK patterns are HIV terms, so any other disease area yields nothing.

    Without a population or a setting there is no APPLICABILITY role, and the serving role
    gate requires one - so an HIV-specific vocabulary alone would block every other corpus.
    """

    scope = _narrative_applicability(
        "Adults with type 2 diabetes attending primary health care should be offered "
        "annual renal function testing. Metformin is contraindicated in severe renal "
        "impairment."
    )

    assert any("adult" in item for item in scope.population)
    assert any("primary" in item for item in scope.care_settings)
    assert scope.exclusion_criteria


def test_hiv_applicability_still_resolves_on_the_narrative_path() -> None:
    """The new vocabulary is broader, not a replacement that drops the original domain."""

    scope = _narrative_applicability(
        "Pregnant women living with HIV attending antenatal care should receive "
        "antiretroviral therapy for life."
    )

    assert scope.population
    assert scope.care_settings
