from collections import defaultdict
from datetime import date

from app.schemas.domain import (
    LifecycleRelationship,
    RelationshipType,
    SourceStatus,
    SourceVersion,
)

INVALIDATING_RELATIONSHIPS = {
    RelationshipType.SUPERSEDES,
    RelationshipType.PARTIALLY_SUPERSEDES,
    RelationshipType.AMENDS,
    RelationshipType.CORRECTS,
    RelationshipType.WITHDRAWS,
    RelationshipType.REPLACES_SECTION,
}


class LifecycleCycleError(ValueError):
    pass


class LifecycleRecordConflictError(ValueError):
    pass


class SourceVersionNotFoundError(KeyError):
    pass


class SourceLifecycleGraph:
    def __init__(self) -> None:
        self._versions: dict[str, SourceVersion] = {}
        self._relationships: dict[str, LifecycleRelationship] = {}
        self._outgoing: dict[str, list[LifecycleRelationship]] = defaultdict(list)

    def add_version(self, version: SourceVersion) -> None:
        existing = self._versions.get(version.source_version_id)
        if existing is not None:
            if existing == version:
                return
            raise LifecycleRecordConflictError(
                f"source version ID already has a different record: "
                f"{version.source_version_id}"
            )
        self._versions[version.source_version_id] = version

    def add_relationship(self, relationship: LifecycleRelationship) -> None:
        existing = self._relationships.get(relationship.source_relationship_id)
        if existing is not None:
            if existing == relationship:
                return
            raise LifecycleRecordConflictError(
                f"relationship ID already has a different record: "
                f"{relationship.source_relationship_id}"
            )
        source = relationship.from_source_version_id
        target = relationship.to_source_version_id
        if source not in self._versions or target not in self._versions:
            raise ValueError("both source versions must exist before adding a relationship")
        if source == target or self._path_exists(target, source):
            raise LifecycleCycleError("source relationship would create a lifecycle cycle")
        self._relationships[relationship.source_relationship_id] = relationship
        self._outgoing[source].append(relationship)

    def is_evidence_effective(
        self,
        source_version_id: str,
        *,
        as_of: date,
        evidence_id: str | None = None,
        section_id: str | None = None,
        recommendation_id: str | None = None,
        current_mode: bool = True,
    ) -> bool:
        try:
            version = self._versions[source_version_id]
        except KeyError as error:
            raise SourceVersionNotFoundError(source_version_id) from error
        if not version.approved_for_retrieval:
            return False
        if current_mode and version.status not in {
            SourceStatus.EFFECTIVE,
            SourceStatus.PARTIALLY_SUPERSEDED,
        }:
            return False
        if version.effective_from and as_of < version.effective_from:
            return False
        if version.effective_to and as_of > version.effective_to:
            return False

        for relationship in self._outgoing[source_version_id]:
            if relationship.relationship_type not in INVALIDATING_RELATIONSHIPS:
                continue
            if not self._relationship_active(relationship, as_of):
                continue
            if self._relationship_affects(
                relationship,
                evidence_id=evidence_id,
                section_id=section_id,
                recommendation_id=recommendation_id,
            ):
                return False
        return True

    def _path_exists(self, start: str, target: str) -> bool:
        pending = [start]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(
                edge.to_source_version_id for edge in self._outgoing.get(current, [])
            )
        return False

    @staticmethod
    def _relationship_active(relationship: LifecycleRelationship, as_of: date) -> bool:
        if relationship.valid_from and as_of < relationship.valid_from:
            return False
        return not relationship.valid_to or as_of <= relationship.valid_to

    @staticmethod
    def _relationship_affects(
        relationship: LifecycleRelationship,
        *,
        evidence_id: str | None,
        section_id: str | None,
        recommendation_id: str | None,
    ) -> bool:
        scoped = any(
            (
                relationship.affected_evidence_ids,
                relationship.affected_section_ids,
                relationship.affected_recommendation_ids,
            )
        )
        if not scoped:
            return True
        return bool(
            (evidence_id and evidence_id in relationship.affected_evidence_ids)
            or (section_id and section_id in relationship.affected_section_ids)
            or (
                recommendation_id
                and recommendation_id in relationship.affected_recommendation_ids
            )
        )
