"""Persistence boundary for immutable Phase 4 QA facts and decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.materialization_schemas import (
    ANY_AUTHORITY_BINDING,
    AnyAuthorityBinding,
    MaterializedEvidenceRecord,
    SignedCorpusReleaseCandidate,
)
from app.corpus_steward.qa_schemas import (
    AnchorReplayResult,
    QARunState,
    SignedEvidenceArtifactManifest,
    SignedQADecisionBatch,
)
from app.corpus_steward.schemas import ReconciliationReleaseCandidate, TrustRootDefinition
from app.persistence.database import Database
from app.persistence.models import (
    CorpusQARunRow,
    EvidenceAnchorReplayRow,
    EvidenceQADecisionRow,
    MaterializationRunRow,
    MaterializedEvidenceRow,
    PublisherDomainRow,
    PublisherRow,
    ReconciliationReleaseCandidateRow,
    SourceRow,
    SourceVersionRow,
    StewardArtifactRow,
)
from app.schemas.corpus import CorpusEvidenceRecord, CorpusReleaseBundle
from app.schemas.domain import utc_now


class QARepositoryError(RuntimeError):
    pass


class QARepositoryConflictError(QARepositoryError):
    pass


@dataclass(frozen=True)
class MaterializedEvidenceArtifact:
    record: MaterializedEvidenceRecord
    asset_id: str
    source_unit_id: str
    evidence_artifact_sha256: str
    evidence_storage_key: str
    source_storage_key: str


@dataclass(frozen=True)
class QACandidateContext:
    materialization_run_id: str
    candidate: SignedCorpusReleaseCandidate
    authority_binding: AnyAuthorityBinding
    reconciliation: ReconciliationReleaseCandidate
    evidence: tuple[MaterializedEvidenceArtifact, ...]


@dataclass(frozen=True)
class StoredQARun:
    qa_run_id: str
    state: QARunState
    manifest: SignedEvidenceArtifactManifest
    manifest_artifact_sha256: str
    evidence_count: int
    decision_batch: SignedQADecisionBatch | None
    approved_count: int
    quarantined_count: int
    corpus_release_id: str | None
    bundle_sha256: str | None
    bundle_artifact_sha256: str | None


def canonical_source_id(asset_id: str) -> str:
    if len(asset_id) <= 64:
        return asset_id
    return f"SRC_{hashlib.sha256(asset_id.encode()).hexdigest()[:32]}"


def canonical_source_version_id(asset_id: str, materialized_version_id: str) -> str:
    if len(materialized_version_id) <= 64:
        return materialized_version_id
    value = f"{asset_id}\0{materialized_version_id}".encode()
    return f"SV_{hashlib.sha256(value).hexdigest()[:32]}"


class SQLQARepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def candidate_context(self, corpus_candidate_id: str) -> QACandidateContext:
        async with self._database.session() as session:
            run_rows = tuple(
                await session.scalars(
                    select(MaterializationRunRow).where(
                        MaterializationRunRow.state == "READY_FOR_QA",
                        MaterializationRunRow.corpus_candidate.is_not(None),
                    )
                )
            )
            matches: list[tuple[MaterializationRunRow, SignedCorpusReleaseCandidate]] = []
            for run in run_rows:
                candidate = SignedCorpusReleaseCandidate.model_validate(run.corpus_candidate)
                if candidate.content.corpus_release_candidate_id == corpus_candidate_id:
                    matches.append((run, candidate))
            if len(matches) != 1:
                if not matches:
                    raise QARepositoryError(
                        f"ready corpus release candidate not found: {corpus_candidate_id}"
                    )
                raise QARepositoryError("corpus release candidate is not unique")
            run, candidate = matches[0]
            if candidate.candidate_sha256 != run.corpus_candidate_sha256:
                raise QARepositoryError("stored corpus release candidate digest is inconsistent")
            # A stored JSON column, so the widened annotation above reaches this parse
            # only through the adapter. Naming one member here would materialize a
            # narrative binding successfully and then reject it at QA.
            binding = ANY_AUTHORITY_BINDING.validate_python(run.authority_binding)
            if binding.binding_sha256 != run.authority_binding_sha256:
                raise QARepositoryError("stored authority binding digest is inconsistent")
            reconciliation_row = await session.get(
                ReconciliationReleaseCandidateRow, run.reconciliation_candidate_id
            )
            if reconciliation_row is None:
                raise QARepositoryError("reconciliation candidate is missing")
            reconciliation = ReconciliationReleaseCandidate.model_validate(
                reconciliation_row.payload
            )
            if reconciliation.candidate_sha256 != reconciliation_row.candidate_sha256:
                raise QARepositoryError("reconciliation candidate digest is inconsistent")

            rows = tuple(
                await session.scalars(
                    select(MaterializedEvidenceRow)
                    .where(
                        MaterializedEvidenceRow.materialization_run_id == run.materialization_run_id
                    )
                    .order_by(MaterializedEvidenceRow.evidence_id)
                )
            )
            if len(rows) != run.evidence_count:
                raise QARepositoryError("materialized evidence count is inconsistent")
            declared = dict(candidate.content.evidence_entries)
            actual = {row.evidence_id: row.evidence_sha256 for row in rows}
            if declared != actual:
                raise QARepositoryError(
                    "candidate manifest and materialized evidence membership differ"
                )

            artifact_hashes = {
                digest
                for row in rows
                for digest in (row.artifact_sha256, row.source_artifact_sha256)
            }
            artifacts: dict[str, StewardArtifactRow] = {}
            ordered_hashes = sorted(artifact_hashes)
            for offset in range(0, len(ordered_hashes), 500):
                found = tuple(
                    await session.scalars(
                        select(StewardArtifactRow).where(
                            StewardArtifactRow.sha256.in_(ordered_hashes[offset : offset + 500])
                        )
                    )
                )
                artifacts.update({item.sha256: item for item in found})
            if set(artifacts) != artifact_hashes:
                raise QARepositoryError("QA evidence artifact is not preserved")

            evidence: list[MaterializedEvidenceArtifact] = []
            for row in rows:
                record = MaterializedEvidenceRecord.model_validate(row.payload)
                if (
                    record.content.evidence_id != row.evidence_id
                    or record.evidence_sha256 != row.evidence_sha256
                    or record.content.asset_id != row.asset_id
                    or record.content.source_unit_id != row.source_unit_id
                    or record.content.source_artifact_sha256 != row.source_artifact_sha256
                    or artifacts[row.artifact_sha256].kind != "EVIDENCE_RECORD"
                    or artifacts[row.source_artifact_sha256].kind != "NARRATIVE_SOURCE"
                ):
                    raise QARepositoryError("materialized evidence row is inconsistent")
                evidence.append(
                    MaterializedEvidenceArtifact(
                        record=record,
                        asset_id=row.asset_id,
                        source_unit_id=row.source_unit_id,
                        evidence_artifact_sha256=row.artifact_sha256,
                        evidence_storage_key=artifacts[row.artifact_sha256].storage_key,
                        source_storage_key=artifacts[row.source_artifact_sha256].storage_key,
                    )
                )
            return QACandidateContext(
                materialization_run_id=run.materialization_run_id,
                candidate=candidate,
                authority_binding=binding,
                reconciliation=reconciliation,
                evidence=tuple(evidence),
            )

    async def existing(self, corpus_candidate_id: str) -> StoredQARun | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(CorpusQARunRow).where(
                    CorpusQARunRow.corpus_release_candidate_id == corpus_candidate_id
                )
            )
            if row is None:
                return None
            manifest = SignedEvidenceArtifactManifest.model_validate(row.evidence_manifest)
            decision_batch = (
                SignedQADecisionBatch.model_validate(row.decision_batch)
                if row.decision_batch is not None
                else None
            )
            if (
                manifest.content.qa_run_id != row.qa_run_id
                or manifest.manifest_sha256 != row.evidence_manifest_sha256
                or len(manifest.content.entries) != row.evidence_count
                or (decision_batch.batch_sha256 if decision_batch else None)
                != row.decision_batch_sha256
            ):
                raise QARepositoryError("stored QA run is inconsistent")
            return StoredQARun(
                qa_run_id=row.qa_run_id,
                state=QARunState(row.state),
                manifest=manifest,
                manifest_artifact_sha256=row.evidence_manifest_artifact_sha256,
                evidence_count=row.evidence_count,
                decision_batch=decision_batch,
                approved_count=row.approved_count,
                quarantined_count=row.quarantined_count,
                corpus_release_id=row.corpus_release_id,
                bundle_sha256=row.bundle_sha256,
                bundle_artifact_sha256=row.bundle_artifact_sha256,
            )

    async def replays(self, qa_run_id: str) -> tuple[AnchorReplayResult, ...]:
        async with self._database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(EvidenceAnchorReplayRow)
                    .where(EvidenceAnchorReplayRow.qa_run_id == qa_run_id)
                    .order_by(EvidenceAnchorReplayRow.evidence_id)
                )
            )
            results = tuple(AnchorReplayResult.model_validate(row.payload) for row in rows)
            if any(
                result.sha256 != row.replay_sha256
                for result, row in zip(results, rows, strict=True)
            ):
                raise QARepositoryError("stored anchor replay digest is inconsistent")
            return results

    async def record_preparation(
        self,
        *,
        manifest: SignedEvidenceArtifactManifest,
        manifest_artifact_sha256: str,
        replays: tuple[AnchorReplayResult, ...],
    ) -> None:
        content = manifest.content
        replay_ids = {item.evidence_id for item in replays}
        entry_ids = {item.evidence_id for item in content.entries}
        if replay_ids != entry_ids:
            raise QARepositoryError("anchor replay membership differs from artifact manifest")
        try:
            async with self._database.session() as session:
                existing = await session.get(CorpusQARunRow, content.qa_run_id)
                if existing is not None:
                    if existing.evidence_manifest_sha256 != manifest.manifest_sha256:
                        raise QARepositoryConflictError(
                            "QA run ID identifies another evidence manifest"
                        )
                    return
                session.add(
                    CorpusQARunRow(
                        qa_run_id=content.qa_run_id,
                        corpus_release_candidate_id=content.corpus_release_candidate_id,
                        materialization_run_id=content.materialization_run_id,
                        corpus_release_candidate_sha256=(content.corpus_release_candidate_sha256),
                        state=QARunState.PREPARED.value,
                        evidence_manifest_sha256=manifest.manifest_sha256,
                        evidence_manifest=manifest.model_dump(mode="json"),
                        evidence_manifest_artifact_sha256=manifest_artifact_sha256,
                        evidence_manifest_attestation_id=manifest.attestation.attestation_id,
                        evidence_count=len(content.entries),
                        decision_batch_sha256=None,
                        decision_batch=None,
                        decision_batch_artifact_sha256=None,
                        decision_batch_attestation_id=None,
                        approved_count=0,
                        quarantined_count=0,
                        corpus_release_id=None,
                        bundle_sha256=None,
                        bundle_artifact_sha256=None,
                        started_at=content.created_at,
                        completed_at=None,
                    )
                )
                session.add_all(
                    EvidenceAnchorReplayRow(
                        qa_run_id=content.qa_run_id,
                        evidence_id=item.evidence_id,
                        materialized_evidence_sha256=item.materialized_evidence_sha256,
                        source_artifact_sha256=item.source_artifact_sha256,
                        replay_sha256=item.sha256,
                        outcome="PASS" if item.passed else "FAIL",
                        payload=item.model_dump(mode="json"),
                        replayed_at=item.replayed_at,
                    )
                    for item in replays
                )
        except IntegrityError as error:
            raise QARepositoryConflictError("QA preparation registry conflict") from error

    async def register_promoted_sources(
        self,
        *,
        evidence: tuple[CorpusEvidenceRecord, ...],
        authority_binding: AnyAuthorityBinding,
        trust_root: TrustRootDefinition,
    ) -> None:
        assets = {item.asset_id: item for item in authority_binding.content.assets}
        now = utc_now()
        async with self._database.session() as session:
            publisher = await session.get(PublisherRow, trust_root.publisher_id)
            if publisher is None:
                session.add(
                    PublisherRow(
                        publisher_id=trust_root.publisher_id,
                        name=trust_root.publisher_name,
                        created_at=now,
                    )
                )
            elif publisher.name != trust_root.publisher_name:
                raise QARepositoryConflictError("publisher registry metadata conflicts")

            for domain in trust_root.allowed_domains:
                domain_row = await session.get(PublisherDomainRow, domain)
                if domain_row is None:
                    session.add(
                        PublisherDomainRow(domain=domain, publisher_id=trust_root.publisher_id)
                    )
                elif domain_row.publisher_id != trust_root.publisher_id:
                    raise QARepositoryConflictError("publisher domain belongs to another publisher")

            representative: dict[str, CorpusEvidenceRecord] = {}
            for item in evidence:
                representative.setdefault(item.source_id, item)
            for source_id, item in representative.items():
                asset_id = next(
                    asset_id for asset_id in assets if canonical_source_id(asset_id) == source_id
                )
                asset = assets[asset_id]
                source = await session.get(SourceRow, source_id)
                expected_source = {
                    "publisher_id": item.publisher_id,
                    "title": asset.title,
                    "source_class": "E1",
                    "jurisdiction": item.jurisdiction,
                    "canonical_url": asset.source_uri,
                    # The evidence flag is the *excerpt* permission - "may this passage
                    # text be shown" - which is how the serving projection reads it
                    # (corpus/releases.py). It is not the page-image permission, and
                    # binding it to `license_render_allowed` here was the conflation
                    # migration 0015 split the column to end: this line was simply not
                    # updated with it. Asserting the wrong half made a source cleared for
                    # excerpts but not for page reproduction - which is every WHO source
                    # under branch A, docs/rendering-licence.md#decision - unable to pass
                    # QA at all, and would have inserted a *new* source with page
                    # reproduction granted and excerpts denied, inverted on both axes.
                    #
                    # `license_render_allowed` is deliberately absent rather than moved.
                    # Nothing about a piece of evidence licenses reproducing the page it
                    # came from; that is a source-level decision, taken explicitly through
                    # `set-source-licence` and left to its fail-closed default until
                    # someone takes it.
                    "license_excerpt_allowed": item.render_allowed,
                }
                if source is None:
                    session.add(SourceRow(source_id=source_id, created_at=now, **expected_source))
                elif any(getattr(source, key) != value for key, value in expected_source.items()):
                    raise QARepositoryConflictError("canonical source metadata conflicts")

            versions: dict[str, CorpusEvidenceRecord] = {}
            for item in evidence:
                versions.setdefault(item.source_version_id, item)
            for version_id, item in versions.items():
                version = await session.get(SourceVersionRow, version_id)
                if version is None:
                    session.add(
                        SourceVersionRow(
                            source_version_id=version_id,
                            source_id=item.source_id,
                            version_label=version_id,
                            status=item.lifecycle_status.value,
                            effective_from=None,
                            effective_to=None,
                            approved_for_retrieval=True,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                elif (
                    version.source_id != item.source_id
                    or version.status != item.lifecycle_status.value
                    or not version.approved_for_retrieval
                ):
                    raise QARepositoryConflictError("canonical source version metadata conflicts")

    async def record_completion(
        self,
        *,
        batch: SignedQADecisionBatch,
        batch_artifact_sha256: str,
        bundle: CorpusReleaseBundle,
        bundle_sha256: str,
        bundle_artifact_sha256: str,
    ) -> None:
        content = batch.content
        approved = sum(item.disposition.value == "APPROVE" for item in content.decisions)
        quarantined = len(content.decisions) - approved
        release_id = bundle.manifest.content.corpus_release_id
        try:
            async with self._database.session() as session:
                run = await session.get(CorpusQARunRow, content.qa_run_id)
                if run is None:
                    raise QARepositoryError("QA run is missing")
                if run.state == QARunState.VALIDATED.value:
                    if (
                        run.decision_batch_sha256 != batch.batch_sha256
                        or run.bundle_sha256 != bundle_sha256
                    ):
                        raise QARepositoryConflictError("validated QA run cannot be changed")
                    return
                if run.evidence_count != len(content.decisions):
                    raise QARepositoryError("decision batch does not cover the QA run")
                session.add_all(
                    EvidenceQADecisionRow(
                        qa_run_id=content.qa_run_id,
                        evidence_id=item.evidence_id,
                        materialized_evidence_sha256=item.materialized_evidence_sha256,
                        decision_sha256=item.sha256,
                        disposition=item.disposition.value,
                        decision_authority=content.decision_authority,
                        batch_attestation_id=batch.attestation.attestation_id,
                        payload=item.model_dump(mode="json"),
                        decided_at=content.decided_at,
                    )
                    for item in content.decisions
                )
                run.decision_batch_sha256 = batch.batch_sha256
                run.decision_batch = batch.model_dump(mode="json")
                run.decision_batch_artifact_sha256 = batch_artifact_sha256
                run.decision_batch_attestation_id = batch.attestation.attestation_id
                run.approved_count = approved
                run.quarantined_count = quarantined
                run.corpus_release_id = release_id
                run.bundle_sha256 = bundle_sha256
                run.bundle_artifact_sha256 = bundle_artifact_sha256
                run.state = QARunState.VALIDATED.value
                run.completed_at = content.decided_at
        except IntegrityError as error:
            raise QARepositoryConflictError("QA completion registry conflict") from error
