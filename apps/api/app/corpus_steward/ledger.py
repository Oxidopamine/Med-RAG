from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.schemas import (
    AttestationPurpose,
    CoverageExceptionContent,
    InventoryChange,
    InventoryChangeKind,
    InventoryItem,
    JobState,
    ReconciliationDisposition,
    ReconciliationReleaseCandidate,
    ReconciliationStage,
    SignedExceptionReference,
    StageState,
    TrustRootDefinition,
    VerifiedAttestationReference,
)
from app.corpus_steward.storage import StoredStewardArtifact
from app.persistence.database import Database
from app.persistence.models import (
    CryptographicAttestationRow,
    ReconciliationExceptionRow,
    ReconciliationInventoryItemRow,
    ReconciliationInventoryRow,
    ReconciliationJobAttemptRow,
    ReconciliationJobRow,
    ReconciliationReleaseCandidateRow,
    ReconciliationStageRow,
    StewardArtifactRow,
    TrustRootRow,
)
from app.schemas.domain import utc_now


class ReconciliationLedgerError(RuntimeError):
    pass


class ReconciliationConflictError(ReconciliationLedgerError):
    pass


@dataclass(frozen=True)
class JobAttempt:
    job_id: str
    attempt_id: str
    attempt_number: int
    resumed_from_stage: ReconciliationStage | None
    state: JobState


@dataclass(frozen=True)
class PreviousArtifact:
    artifact_sha256: str
    storage_key: str
    byte_size: int
    media_type: str
    etag: str | None
    last_modified: str | None
    requested_url: str
    final_url: str
    fetched_at: datetime


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class SQLReconciliationLedger:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def start_job(
        self, trust_root: TrustRootDefinition, *, idempotency_key: str
    ) -> JobAttempt:
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        now = utc_now()
        async with self._database.session() as session:
            job = await session.scalar(
                select(ReconciliationJobRow).where(
                    ReconciliationJobRow.trust_root_id == trust_root.trust_root_id,
                    ReconciliationJobRow.idempotency_key == idempotency_key,
                )
            )
            if job is None:
                job = ReconciliationJobRow(
                    job_id=f"JOB_{uuid4().hex}",
                    trust_root_id=trust_root.trust_root_id,
                    idempotency_key=idempotency_key,
                    trust_root_sha256=trust_root.sha256,
                    connector_name=trust_root.connector_name,
                    connector_version=trust_root.connector_version,
                    state=JobState.PENDING.value,
                    current_stage=None,
                    blockers=[],
                    last_error=None,
                    started_at=now,
                    completed_at=None,
                    updated_at=now,
                )
                session.add(job)
                await session.flush()
            elif (
                job.trust_root_sha256 != trust_root.sha256
                or job.connector_version != trust_root.connector_version
            ):
                raise ReconciliationConflictError(
                    "idempotency key belongs to a different trust-root definition"
                )

            if job.state == JobState.COMPLETED.value:
                return JobAttempt(
                    job_id=job.job_id,
                    attempt_id="",
                    attempt_number=0,
                    resumed_from_stage=None,
                    state=JobState.COMPLETED,
                )
            completed_stages = set(
                (
                    await session.scalars(
                        select(ReconciliationStageRow.stage).where(
                            ReconciliationStageRow.job_id == job.job_id,
                            ReconciliationStageRow.state == StageState.COMPLETED.value,
                        )
                    )
                ).all()
            )
            resumed = next(
                (stage for stage in ReconciliationStage if stage.value not in completed_stages),
                None,
            )
            count = await session.scalar(
                select(func.count())
                .select_from(ReconciliationJobAttemptRow)
                .where(ReconciliationJobAttemptRow.job_id == job.job_id)
            )
            attempt_number = int(count or 0) + 1
            attempt = ReconciliationJobAttemptRow(
                attempt_id=f"TRY_{uuid4().hex}",
                job_id=job.job_id,
                attempt_number=attempt_number,
                resumed_from_stage=resumed.value if resumed else None,
                state=JobState.RUNNING.value,
                error=None,
                started_at=now,
                finished_at=None,
            )
            session.add(attempt)
            job.state = JobState.RUNNING.value
            job.current_stage = resumed.value if resumed else None
            job.blockers = []
            job.last_error = None
            job.completed_at = None
            job.updated_at = now
            return JobAttempt(
                job_id=job.job_id,
                attempt_id=attempt.attempt_id,
                attempt_number=attempt_number,
                resumed_from_stage=resumed,
                state=JobState.RUNNING,
            )

    async def begin_stage(self, job_id: str, stage: ReconciliationStage) -> bool:
        now = utc_now()
        async with self._database.session() as session:
            row = await session.scalar(
                select(ReconciliationStageRow).where(
                    ReconciliationStageRow.job_id == job_id,
                    ReconciliationStageRow.stage == stage.value,
                )
            )
            if row is not None and row.state == StageState.COMPLETED.value:
                return False
            if row is None:
                row = ReconciliationStageRow(
                    stage_run_id=f"STG_{uuid4().hex}",
                    job_id=job_id,
                    stage=stage.value,
                    state=StageState.RUNNING.value,
                    attempt_count=1,
                    input_sha256=None,
                    output_sha256=None,
                    output=None,
                    attestation_id=None,
                    error=None,
                    started_at=now,
                    completed_at=None,
                )
                session.add(row)
            else:
                row.state = StageState.RUNNING.value
                row.attempt_count += 1
                row.error = None
                row.started_at = now
                row.completed_at = None
            job = await session.get(ReconciliationJobRow, job_id)
            if job is None:
                raise ReconciliationLedgerError(f"job not found: {job_id}")
            job.current_stage = stage.value
            job.updated_at = now
            return True

    async def complete_stage(
        self,
        job_id: str,
        stage: ReconciliationStage,
        *,
        input_sha256: str,
        output_sha256: str,
        output: dict[str, Any],
        attestation: VerifiedAttestationReference,
    ) -> None:
        if attestation.purpose is not AttestationPurpose.STAGE:
            raise ReconciliationLedgerError("stage completion requires a stage attestation")
        now = utc_now()
        async with self._database.session() as session:
            row = await session.scalar(
                select(ReconciliationStageRow).where(
                    ReconciliationStageRow.job_id == job_id,
                    ReconciliationStageRow.stage == stage.value,
                )
            )
            if row is None or row.state != StageState.RUNNING.value:
                raise ReconciliationLedgerError("stage is not running")
            if attestation.statement_sha256 == output_sha256:
                raise ReconciliationLedgerError(
                    "attestation digest must bind the stage statement, not just its output"
                )
            row.state = StageState.COMPLETED.value
            row.input_sha256 = input_sha256
            row.output_sha256 = output_sha256
            row.output = output
            row.attestation_id = attestation.attestation_id
            row.error = None
            row.completed_at = now

    async def fail_stage(
        self,
        job_id: str,
        attempt_id: str,
        stage: ReconciliationStage,
        error: str,
    ) -> None:
        now = utc_now()
        async with self._database.session() as session:
            row = await session.scalar(
                select(ReconciliationStageRow).where(
                    ReconciliationStageRow.job_id == job_id,
                    ReconciliationStageRow.stage == stage.value,
                )
            )
            if row is not None:
                row.state = StageState.FAILED.value
                row.error = error
                row.completed_at = now
            job = await session.get(ReconciliationJobRow, job_id)
            attempt = await session.get(ReconciliationJobAttemptRow, attempt_id)
            if job is not None:
                job.state = JobState.FAILED.value
                job.last_error = error
                job.updated_at = now
            if attempt is not None:
                attempt.state = JobState.FAILED.value
                attempt.error = error
                attempt.finished_at = now

    async def finish_job(
        self,
        job_id: str,
        attempt_id: str,
        *,
        blockers: list[str],
    ) -> JobState:
        now = utc_now()
        state = JobState.BLOCKED if blockers else JobState.COMPLETED
        async with self._database.session() as session:
            job = await session.get(ReconciliationJobRow, job_id)
            attempt = await session.get(ReconciliationJobAttemptRow, attempt_id)
            if job is None or attempt is None:
                raise ReconciliationLedgerError("job attempt not found")
            job.state = state.value
            job.current_stage = None
            job.blockers = sorted(blockers)
            job.last_error = None
            job.completed_at = now
            job.updated_at = now
            attempt.state = state.value
            attempt.finished_at = now
            root = await session.get(TrustRootRow, job.trust_root_id)
            if root is not None and state is JobState.COMPLETED:
                root.last_reconciled_at = now
        return state

    async def stage_output(self, job_id: str, stage: ReconciliationStage) -> dict[str, Any] | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(ReconciliationStageRow).where(
                    ReconciliationStageRow.job_id == job_id,
                    ReconciliationStageRow.stage == stage.value,
                    ReconciliationStageRow.state == StageState.COMPLETED.value,
                )
            )
            return row.output if row is not None else None

    async def completed_stage_attestations(
        self, job_id: str
    ) -> tuple[VerifiedAttestationReference, ...]:
        async with self._database.session() as session:
            rows = (
                await session.scalars(
                    select(CryptographicAttestationRow)
                    .join(
                        ReconciliationStageRow,
                        ReconciliationStageRow.attestation_id
                        == CryptographicAttestationRow.attestation_id,
                    )
                    .where(
                        ReconciliationStageRow.job_id == job_id,
                        ReconciliationStageRow.state == StageState.COMPLETED.value,
                    )
                    .order_by(ReconciliationStageRow.stage)
                )
            ).all()
            return tuple(self._attestation_reference(row) for row in rows)

    async def record_artifact(self, artifact: StoredStewardArtifact) -> None:
        await self.record_artifacts((artifact,))

    async def record_artifacts(self, artifacts: tuple[StoredStewardArtifact, ...]) -> None:
        """Register a content-addressed batch in one transaction."""

        if not artifacts:
            return
        by_digest = {item.sha256: item for item in artifacts}
        if len(by_digest) != len(artifacts):
            for item in artifacts:
                if not self._artifact_matches_value(by_digest[item.sha256], item):
                    raise ReconciliationConflictError(
                        "artifact batch repeats a digest with conflicting metadata"
                    )
        now = utc_now()
        try:
            async with self._database.session() as session:
                existing: dict[str, StewardArtifactRow] = {}
                digests = sorted(by_digest)
                for offset in range(0, len(digests), 500):
                    rows = tuple(
                        await session.scalars(
                            select(StewardArtifactRow).where(
                                StewardArtifactRow.sha256.in_(digests[offset : offset + 500])
                            )
                        )
                    )
                    existing.update({row.sha256: row for row in rows})
                for digest, row in existing.items():
                    if not self._artifact_matches(row, by_digest[digest]):
                        raise ReconciliationConflictError(
                            "artifact digest already has conflicting metadata"
                        )
                session.add_all(
                    StewardArtifactRow(
                        sha256=artifact.sha256,
                        kind=artifact.kind.value,
                        byte_size=artifact.byte_size,
                        media_type=artifact.media_type,
                        storage_key=artifact.storage_key,
                        created_at=now,
                    )
                    for digest, artifact in by_digest.items()
                    if digest not in existing
                )
        except IntegrityError as error:
            # A uniqueness race is idempotent only if every committed row matches.
            async with self._database.session() as session:
                rows = tuple(
                    await session.scalars(
                        select(StewardArtifactRow).where(
                            StewardArtifactRow.sha256.in_(tuple(by_digest))
                        )
                    )
                )
                if len(rows) == len(by_digest) and all(
                    self._artifact_matches(row, by_digest[row.sha256]) for row in rows
                ):
                    return
            raise ReconciliationConflictError("artifact registry conflict") from error

    @staticmethod
    def _artifact_matches_value(left: StoredStewardArtifact, right: StoredStewardArtifact) -> bool:
        return (
            left.byte_size == right.byte_size
            and left.storage_key == right.storage_key
            and left.media_type == right.media_type
            and left.kind is right.kind
        )

    @staticmethod
    def _artifact_matches(row: StewardArtifactRow, artifact: StoredStewardArtifact) -> bool:
        return (
            row.byte_size == artifact.byte_size
            and row.storage_key == artifact.storage_key
            and row.media_type == artifact.media_type
            and row.kind == artifact.kind.value
        )

    async def previous_inventory_items(
        self, trust_root_id: str, *, exclude_job_id: str
    ) -> dict[str, ReconciliationInventoryItemRow]:
        async with self._database.session() as session:
            inventory = await session.scalar(
                select(ReconciliationInventoryRow)
                .where(
                    ReconciliationInventoryRow.trust_root_id == trust_root_id,
                    ReconciliationInventoryRow.job_id != exclude_job_id,
                )
                .order_by(ReconciliationInventoryRow.cutoff_at.desc())
                .limit(1)
            )
            if inventory is None:
                return {}
            rows = (
                await session.scalars(
                    select(ReconciliationInventoryItemRow).where(
                        ReconciliationInventoryItemRow.inventory_id == inventory.inventory_id
                    )
                )
            ).all()
            session.expunge_all()
            return {row.item_id: row for row in rows}

    async def create_inventory(
        self,
        *,
        job_id: str,
        trust_root_id: str,
        cutoff_at: datetime,
        inventory_artifact_sha256: str,
        items: tuple[InventoryItem, ...],
        changes: tuple[InventoryChange, ...],
    ) -> str:
        changes_by_id = {change.item_id: change for change in changes}
        now = utc_now()
        async with self._database.session() as session:
            existing = await session.scalar(
                select(ReconciliationInventoryRow).where(
                    ReconciliationInventoryRow.job_id == job_id
                )
            )
            if existing is not None:
                return existing.inventory_id
            inventory = ReconciliationInventoryRow(
                inventory_id=f"INV_{uuid4().hex}",
                job_id=job_id,
                trust_root_id=trust_root_id,
                cutoff_at=cutoff_at,
                inventory_artifact_sha256=inventory_artifact_sha256,
                item_count=len(items),
                complete=False,
                created_at=now,
            )
            session.add(inventory)
            await session.flush()
            for item in items:
                change = changes_by_id[item.item_id]
                if change.change is InventoryChangeKind.REMOVED:
                    raise ReconciliationLedgerError("removed items are not current inventory rows")
                session.add(
                    ReconciliationInventoryItemRow(
                        inventory_id=inventory.inventory_id,
                        item_id=item.item_id,
                        fingerprint_sha256=item.fingerprint_sha256,
                        item=item.model_dump(mode="json"),
                        change_kind=change.change.value,
                        disposition=ReconciliationDisposition.BLOCKED.value,
                        artifact_sha256=None,
                        etag=None,
                        last_modified=None,
                        requested_url=None,
                        final_url=None,
                        fetched_at=None,
                        blocker=None,
                    )
                )
            return inventory.inventory_id

    async def previous_artifact(
        self, trust_root_id: str, item_id: str, *, exclude_job_id: str
    ) -> PreviousArtifact | None:
        async with self._database.session() as session:
            result = await session.execute(
                select(ReconciliationInventoryItemRow, StewardArtifactRow)
                .join(
                    ReconciliationInventoryRow,
                    ReconciliationInventoryRow.inventory_id
                    == ReconciliationInventoryItemRow.inventory_id,
                )
                .join(
                    StewardArtifactRow,
                    StewardArtifactRow.sha256 == ReconciliationInventoryItemRow.artifact_sha256,
                )
                .where(
                    ReconciliationInventoryRow.trust_root_id == trust_root_id,
                    ReconciliationInventoryRow.job_id != exclude_job_id,
                    ReconciliationInventoryItemRow.item_id == item_id,
                    ReconciliationInventoryItemRow.disposition
                    == ReconciliationDisposition.INCLUDED.value,
                )
                .order_by(ReconciliationInventoryRow.cutoff_at.desc())
                .limit(1)
            )
            found = result.one_or_none()
            if found is None:
                return None
            item, artifact = found
            return PreviousArtifact(
                artifact_sha256=artifact.sha256,
                storage_key=artifact.storage_key,
                byte_size=artifact.byte_size,
                media_type=artifact.media_type,
                etag=item.etag,
                last_modified=item.last_modified,
                requested_url=item.requested_url or "",
                final_url=item.final_url or "",
                fetched_at=_as_utc(item.fetched_at),
            )

    async def include_item(
        self,
        inventory_id: str,
        *,
        item_id: str,
        artifact_sha256: str,
        etag: str | None,
        last_modified: str | None,
        requested_url: str,
        final_url: str,
        fetched_at: datetime,
    ) -> None:
        async with self._database.session() as session:
            row = await session.get(
                ReconciliationInventoryItemRow,
                {"inventory_id": inventory_id, "item_id": item_id},
            )
            if row is None:
                raise ReconciliationLedgerError(f"inventory item not found: {item_id}")
            row.disposition = ReconciliationDisposition.INCLUDED.value
            row.artifact_sha256 = artifact_sha256
            row.etag = etag
            row.last_modified = last_modified
            row.requested_url = requested_url
            row.final_url = final_url
            row.fetched_at = fetched_at
            row.blocker = None

    async def block_item(self, inventory_id: str, *, item_id: str, blocker: str) -> None:
        async with self._database.session() as session:
            row = await session.get(
                ReconciliationInventoryItemRow,
                {"inventory_id": inventory_id, "item_id": item_id},
            )
            if row is None:
                raise ReconciliationLedgerError(f"inventory item not found: {item_id}")
            row.disposition = ReconciliationDisposition.BLOCKED.value
            row.blocker = blocker

    async def mark_item_content_changed(self, inventory_id: str, *, item_id: str) -> None:
        async with self._database.session() as session:
            row = await session.get(
                ReconciliationInventoryItemRow,
                {"inventory_id": inventory_id, "item_id": item_id},
            )
            if row is None:
                raise ReconciliationLedgerError(f"inventory item not found: {item_id}")
            if row.change_kind == InventoryChangeKind.UNCHANGED.value:
                row.change_kind = InventoryChangeKind.CHANGED.value

    async def except_item(self, inventory_id: str, *, item_id: str) -> None:
        async with self._database.session() as session:
            row = await session.get(
                ReconciliationInventoryItemRow,
                {"inventory_id": inventory_id, "item_id": item_id},
            )
            if row is None:
                raise ReconciliationLedgerError(f"inventory item not found: {item_id}")
            row.disposition = ReconciliationDisposition.EXCEPTED.value
            row.blocker = None

    async def inventory_rows(self, inventory_id: str) -> tuple[ReconciliationInventoryItemRow, ...]:
        async with self._database.session() as session:
            rows = (
                await session.scalars(
                    select(ReconciliationInventoryItemRow)
                    .where(ReconciliationInventoryItemRow.inventory_id == inventory_id)
                    .order_by(ReconciliationInventoryItemRow.item_id)
                )
            ).all()
            session.expunge_all()
            return tuple(rows)

    async def set_inventory_complete(self, inventory_id: str, complete: bool) -> None:
        async with self._database.session() as session:
            row = await session.get(ReconciliationInventoryRow, inventory_id)
            if row is None:
                raise ReconciliationLedgerError(f"inventory not found: {inventory_id}")
            row.complete = complete

    async def find_active_exception(
        self, trust_root_id: str, item_id: str, *, at: datetime
    ) -> SignedExceptionReference | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(ReconciliationExceptionRow)
                .where(
                    ReconciliationExceptionRow.trust_root_id == trust_root_id,
                    ReconciliationExceptionRow.inventory_item_id == item_id,
                    (
                        ReconciliationExceptionRow.expires_at.is_(None)
                        | (ReconciliationExceptionRow.expires_at > at)
                    ),
                )
                .order_by(ReconciliationExceptionRow.approved_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            attestation = await session.get(CryptographicAttestationRow, row.attestation_id)
            if attestation is None:
                raise ReconciliationLedgerError("exception attestation is missing")
            return SignedExceptionReference(
                exception_id=row.exception_id,
                content=CoverageExceptionContent.model_validate(row.content),
                statement_sha256=row.statement_sha256,
                signature_sha256=row.signature_sha256,
                signer_identity=attestation.signer_identity,
                signing_key_id=attestation.signing_key_id,
            )

    async def record_exception(
        self,
        content: CoverageExceptionContent,
        attestation: VerifiedAttestationReference,
    ) -> SignedExceptionReference:
        if attestation.purpose is not AttestationPurpose.EXCEPTION:
            raise ReconciliationLedgerError("coverage exception requires exception signature")
        now = utc_now()
        async with self._database.session() as session:
            existing = await session.scalar(
                select(ReconciliationExceptionRow).where(
                    ReconciliationExceptionRow.statement_sha256 == attestation.statement_sha256,
                    ReconciliationExceptionRow.signature_sha256 == attestation.signature_sha256,
                )
            )
            if existing is not None:
                return SignedExceptionReference(
                    exception_id=existing.exception_id,
                    content=content,
                    statement_sha256=existing.statement_sha256,
                    signature_sha256=existing.signature_sha256,
                    signer_identity=attestation.signer_identity,
                    signing_key_id=attestation.signing_key_id,
                )
            row = ReconciliationExceptionRow(
                exception_id=f"EXC_{uuid4().hex}",
                trust_root_id=content.trust_root_id,
                inventory_item_id=content.inventory_item_id,
                reason=content.reason,
                content=content.model_dump(mode="json"),
                statement_sha256=attestation.statement_sha256,
                signature_sha256=attestation.signature_sha256,
                attestation_id=attestation.attestation_id,
                approved_at=content.approved_at,
                expires_at=content.expires_at,
                created_at=now,
            )
            session.add(row)
            await session.flush()
            return SignedExceptionReference(
                exception_id=row.exception_id,
                content=content,
                statement_sha256=row.statement_sha256,
                signature_sha256=row.signature_sha256,
                signer_identity=attestation.signer_identity,
                signing_key_id=attestation.signing_key_id,
            )

    async def create_candidate(
        self,
        *,
        job_id: str,
        candidate: ReconciliationReleaseCandidate,
        artifact_sha256: str,
        stage_attestation_id: str,
    ) -> None:
        async with self._database.session() as session:
            existing = await session.scalar(
                select(ReconciliationReleaseCandidateRow).where(
                    ReconciliationReleaseCandidateRow.job_id == job_id
                )
            )
            if existing is not None:
                if existing.candidate_sha256 != candidate.candidate_sha256:
                    raise ReconciliationConflictError(
                        "job already produced a different immutable candidate"
                    )
                return
            session.add(
                ReconciliationReleaseCandidateRow(
                    candidate_id=candidate.content.candidate_id,
                    job_id=job_id,
                    candidate_sha256=candidate.candidate_sha256,
                    payload=candidate.model_dump(mode="json"),
                    artifact_sha256=artifact_sha256,
                    stage_attestation_id=stage_attestation_id,
                    created_at=candidate.content.created_at,
                )
            )

    async def candidate(self, job_id: str) -> ReconciliationReleaseCandidate | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(ReconciliationReleaseCandidateRow).where(
                    ReconciliationReleaseCandidateRow.job_id == job_id
                )
            )
            if row is None:
                return None
            return ReconciliationReleaseCandidate.model_validate(row.payload)

    async def job_state(self, job_id: str) -> tuple[JobState, tuple[str, ...]]:
        async with self._database.session() as session:
            row = await session.get(ReconciliationJobRow, job_id)
            if row is None:
                raise ReconciliationLedgerError(f"job not found: {job_id}")
            return JobState(row.state), tuple(row.blockers)

    @staticmethod
    def _attestation_reference(
        row: CryptographicAttestationRow,
    ) -> VerifiedAttestationReference:
        return VerifiedAttestationReference(
            attestation_id=row.attestation_id,
            purpose=AttestationPurpose(row.purpose),
            predicate_type=row.predicate_type,
            statement_sha256=row.statement_sha256,
            signature_sha256=row.signature_sha256,
            signer_identity=row.signer_identity,
            signing_key_id=row.signing_key_id,
            verified_at=_as_utc(row.verified_at),
        )
