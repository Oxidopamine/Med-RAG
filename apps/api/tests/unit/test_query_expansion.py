import pytest

from app.corpus_steward.query_expansion import (
    CONFLICT_AWARE_QUERY_REVISION,
    EXACT_TERMINOLOGY_REVISION,
    MAX_EXPANSIONS_PER_LANE,
    SAFETY_QUERY_REVISION,
    DeterministicQueryExpander,
    QueryExpansionError,
    QueryExpansionKind,
    QueryExpansionOrigin,
)


def test_exact_terminology_expansion_is_bounded_and_code_preserving() -> None:
    expander = DeterministicQueryExpander(
        terminology_revision=EXACT_TERMINOLOGY_REVISION,
        safety_query_revision=None,
    )
    question = (
        "Retrieve the source definition or mapping for these exact terms: "
        "HIV.B7 HIV HIV.B.DE69 HIV-1"
    )

    first = expander.expand(question)
    second = expander.expand(question)

    assert first == second
    assert first
    assert all(item.kind is QueryExpansionKind.EXACT_TERMINOLOGY for item in first)
    assert any(item.text == "HIV.B7 HIV.B.DE69" for item in first)
    assert any(item.text == "HIV.B.DE69" for item in first)
    assert len(first) <= MAX_EXPANSIONS_PER_LANE


def test_safety_expansion_splits_conflict_sides_without_suite_labels() -> None:
    expander = DeterministicQueryExpander(
        terminology_revision=None,
        safety_query_revision=SAFETY_QUERY_REVISION,
    )
    question = (
        "Retrieve both source passages needed to inspect a potential conflict: "
        "HIV.D4 Screen for TB | HIV.D.DE991 TB screening result date"
    )

    expanded = expander.expand(question)

    assert [item.text for item in expanded[:2]] == [
        "HIV.D4 Screen for TB",
        "HIV.D.DE991 TB screening result date",
    ]
    assert all(item.kind is QueryExpansionKind.SAFETY_QUERY for item in expanded)


def test_unknown_expansion_revision_fails_closed() -> None:
    with pytest.raises(QueryExpansionError, match="unsupported terminology"):
        DeterministicQueryExpander(
            terminology_revision="unsealed-v2",
            safety_query_revision=None,
        )


def test_conflict_aware_expansion_adds_polarity_clauses_deterministically() -> None:
    expander = DeterministicQueryExpander(
        terminology_revision=None,
        safety_query_revision=CONFLICT_AWARE_QUERY_REVISION,
    )
    question = (
        "Retrieve both source passages needed to inspect a potential conflict: "
        "Give treatment, but do not give it during pregnancy. | "
        "Treatment is required if viral load is greater than 1000."
    )

    expanded = expander.expand(question)

    assert [item.text for item in expanded[:2]] == [
        "Give treatment, but do not give it during pregnancy.",
        "Treatment is required if viral load is greater than 1000.",
    ]
    assert "do not give it during pregnancy" in {item.text for item in expanded}
    assert any("required if" in item.text for item in expanded[2:])


def test_conflict_sides_are_labelled_by_origin_not_lane_position() -> None:
    expanded = DeterministicQueryExpander(
        terminology_revision=None,
        safety_query_revision=CONFLICT_AWARE_QUERY_REVISION,
    ).expand(
        "Retrieve both source passages needed to inspect a potential conflict: "
        "Use treatment. | Do not use treatment."
    )

    sides = [
        item for item in expanded if item.origin is QueryExpansionOrigin.CONFLICT_SIDE
    ]
    assert [item.text for item in sides] == ["Use treatment.", "Do not use treatment."]
    assert any(
        item.origin is QueryExpansionOrigin.POLARITY_CLAUSE
        and item.text == "Do not use treatment"
        for item in expanded
    )


def test_unsupported_compact_revision_is_rejected() -> None:
    with pytest.raises(QueryExpansionError):
        DeterministicQueryExpander(
            terminology_revision=None,
            safety_query_revision="clinical-safety-query-v3",
        )


def test_punctuation_only_variants_are_dropped() -> None:
    """A variant with no searchable term would fail a lexical backend outright."""

    expanded = DeterministicQueryExpander(
        terminology_revision=EXACT_TERMINOLOGY_REVISION,
        safety_query_revision=None,
    ).expand("Retrieve the source definition or mapping for these exact terms: '-' HIV.D20")

    assert all(any(char.isalnum() for char in item.text) for item in expanded)
    assert any("HIV.D20" in item.text for item in expanded)
