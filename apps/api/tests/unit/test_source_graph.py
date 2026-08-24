from datetime import date

import pytest

from app.lifecycle.source_graph import (
    LifecycleCycleError,
    LifecycleRecordConflictError,
    SourceLifecycleGraph,
)
from app.schemas.domain import (
    LifecycleRelationship,
    RelationshipType,
    SourceStatus,
    SourceVersion,
)


def version(version_id: str, status: SourceStatus) -> SourceVersion:
    return SourceVersion(
        source_version_id=version_id,
        source_id="SRC_001",
        version_label=version_id,
        status=status,
        effective_from=date(2024, 1, 1),
        approved_for_retrieval=True,
    )


def test_partial_supersession_only_invalidates_affected_scope() -> None:
    graph = SourceLifecycleGraph()
    graph.add_version(version("SV_OLD", SourceStatus.PARTIALLY_SUPERSEDED))
    graph.add_version(version("SV_UPDATE", SourceStatus.EFFECTIVE))
    graph.add_relationship(
        LifecycleRelationship(
            source_relationship_id="REL_001",
            from_source_version_id="SV_OLD",
            to_source_version_id="SV_UPDATE",
            relationship_type=RelationshipType.PARTIALLY_SUPERSEDES,
            valid_from=date(2025, 1, 1),
            affected_section_ids={"SEC_RENAL"},
        )
    )

    assert not graph.is_evidence_effective(
        "SV_OLD", as_of=date(2026, 1, 1), section_id="SEC_RENAL"
    )
    assert graph.is_evidence_effective(
        "SV_OLD", as_of=date(2026, 1, 1), section_id="SEC_OTHER"
    )


def test_lifecycle_relationships_must_remain_acyclic() -> None:
    graph = SourceLifecycleGraph()
    graph.add_version(version("SV_A", SourceStatus.EFFECTIVE))
    graph.add_version(version("SV_B", SourceStatus.EFFECTIVE))
    graph.add_relationship(
        LifecycleRelationship(
            source_relationship_id="REL_A_B",
            from_source_version_id="SV_A",
            to_source_version_id="SV_B",
            relationship_type=RelationshipType.SUPERSEDES,
        )
    )

    with pytest.raises(LifecycleCycleError):
        graph.add_relationship(
            LifecycleRelationship(
                source_relationship_id="REL_B_A",
                from_source_version_id="SV_B",
                to_source_version_id="SV_A",
                relationship_type=RelationshipType.CORRECTS,
            )
        )


def test_unapproved_version_is_never_retrieval_eligible() -> None:
    graph = SourceLifecycleGraph()
    candidate = version("SV_DRAFT", SourceStatus.EFFECTIVE).model_copy(
        update={"approved_for_retrieval": False}
    )
    graph.add_version(candidate)

    assert not graph.is_evidence_effective("SV_DRAFT", as_of=date(2026, 1, 1))


def test_duplicate_version_id_cannot_overwrite_canonical_record() -> None:
    graph = SourceLifecycleGraph()
    graph.add_version(version("SV_001", SourceStatus.EFFECTIVE))

    conflicting = version("SV_001", SourceStatus.WITHDRAWN)

    with pytest.raises(LifecycleRecordConflictError):
        graph.add_version(conflicting)


def test_duplicate_relationship_id_cannot_change_lifecycle_edge() -> None:
    graph = SourceLifecycleGraph()
    graph.add_version(version("SV_A", SourceStatus.PARTIALLY_SUPERSEDED))
    graph.add_version(version("SV_B", SourceStatus.EFFECTIVE))
    graph.add_relationship(
        LifecycleRelationship(
            source_relationship_id="REL_001",
            from_source_version_id="SV_A",
            to_source_version_id="SV_B",
            relationship_type=RelationshipType.PARTIALLY_SUPERSEDES,
            affected_section_ids={"SEC_A"},
        )
    )

    with pytest.raises(LifecycleRecordConflictError):
        graph.add_relationship(
            LifecycleRelationship(
                source_relationship_id="REL_001",
                from_source_version_id="SV_A",
                to_source_version_id="SV_B",
                relationship_type=RelationshipType.PARTIALLY_SUPERSEDES,
                affected_section_ids={"SEC_B"},
            )
        )
