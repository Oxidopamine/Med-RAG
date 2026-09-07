from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.corpus_steward.connectors import connector_for
from app.corpus_steward.connectors.base import (
    ConditionalMetadata,
    ConnectorError,
    HTTPConnectorTransport,
)
from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.ledger import JobAttempt, SQLReconciliationLedger
from app.corpus_steward.registry import (
    AttestationVerificationError,
    SQLAttestationRepository,
    SQLTrustRootRegistry,
)
from app.corpus_steward.schemas import (
    ArtifactKind,
    AttestationPurpose,
    CoverageExceptionContent,
    InventoryChange,
    InventoryChangeKind,
    InventoryEnumeration,
    JobState,
    PreservedResponse,
    ReconciliationCandidateContent,
    ReconciliationDisposition,
    ReconciliationReleaseCandidate,
    ReconciliationReport,
    ReconciliationSnapshot,
    ReconciliationStage,
    SignedExceptionReference,
    SourceArtifactReference,
    StageAttestationContent,
    TrustRootDefinition,
    VerifiedAttestationReference,
)
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_service import NARRATIVE_ANCHORED
from app.schemas.corpus import canonical_json_bytes, canonical_sha256
from app.schemas.domain import utc_now


class ReconciliationService:
    def __init__(
        self,
        *,
        trust_roots: SQLTrustRootRegistry,
        ledger: SQLReconciliationLedger,
        attestations: SQLAttestationRepository,
        artifacts: ImmutableStewardArtifactStore,
        transport: HTTPConnectorTransport,
        signer: Ed25519Signer,
    ) -> None:
        self._trust_roots = trust_roots
        self._ledger = ledger
        self._attestations = attestations
        self._artifacts = artifacts
        self._transport = transport
        self._signer = signer

    async def reconcile(
        self, trust_root_id: str, *, idempotency_key: str
    ) -> ReconciliationReport:
        trust_root = await self._trust_roots.get(trust_root_id)
        # A plain comparison, not the validating helper. Reconciliation asks only whether
        # this is the narrative topology; *validating* the field is the input closure's
        # job, and raising here on an unrecognised value would move the fail-closed point
        # to a stage that does not own the decision and change which stage reports it.
        narrative_topology = (
            trust_root.connector_config.get("source_topology") == NARRATIVE_ANCHORED
        )
        if trust_root.asset_licensing and narrative_topology:
            # A narrative-anchored publisher has no structured companion to name: every
            # inventory item *is* its own clinical asset. Acquisition permission is
            # therefore a property of each licensed asset rather than of one named one,
            # and the gate is a conjunction - a publisher one of whose documents may not
            # be acquired does not get acquired on the strength of the others. Whether a
            # given *item* is licensed at all is checked per item in `_fetch`, because
            # the item list is not known until the connector has enumerated it.
            acquisition_allowed = all(
                policy.acquisition_allowed for policy in trust_root.asset_licensing
            )
        elif trust_root.asset_licensing:
            source_asset_id = trust_root.connector_config.get("structured_asset_id")
            if not isinstance(source_asset_id, str) or not source_asset_id:
                raise ValueError(
                    "per-asset licensing requires connector_config.structured_asset_id"
                )
            acquisition_allowed = trust_root.license_for_asset(
                source_asset_id
            ).acquisition_allowed
        else:
            acquisition_allowed = bool(
                trust_root.licensing_policy
                and trust_root.licensing_policy.acquisition_allowed
            )
        if not acquisition_allowed:
            raise ValueError("trust-root licensing policy does not permit acquisition")
        if self._signer.key_id not in trust_root.trusted_stage_key_ids:
            raise ValueError("signing key is not trusted by this trust root")
        connector = connector_for(
            trust_root.connector_name, trust_root.connector_version
        )
        attempt = await self._ledger.start_job(
            trust_root, idempotency_key=idempotency_key
        )
        if attempt.state is JobState.COMPLETED:
            return await self._report(attempt.job_id, trust_root_id)

        active_stage = ReconciliationStage.ENUMERATE_INVENTORY
        try:
            enumeration_output = await self._enumerate(
                attempt, trust_root, connector
            )
            active_stage = ReconciliationStage.DIFF_INVENTORY
            diff_output = await self._diff(attempt, trust_root, enumeration_output)
            active_stage = ReconciliationStage.FETCH_ARTIFACTS
            fetch_output = await self._fetch(
                attempt, trust_root, connector, enumeration_output, diff_output
            )
            active_stage = ReconciliationStage.RECONCILE
            reconciliation_output = await self._reconcile_gate(
                attempt, trust_root, enumeration_output, diff_output, fetch_output
            )
            active_stage = ReconciliationStage.RELEASE_CANDIDATE
            candidate_output = await self._candidate(
                attempt,
                trust_root,
                diff_output,
                fetch_output,
                reconciliation_output,
            )
        except Exception as error:
            await self._ledger.fail_stage(
                attempt.job_id,
                attempt.attempt_id,
                active_stage,
                f"{type(error).__name__}: {error}",
            )
            raise

        blockers = list(candidate_output.get("blockers", []))
        await self._ledger.finish_job(
            attempt.job_id, attempt.attempt_id, blockers=blockers
        )
        return await self._report(attempt.job_id, trust_root_id)

    async def approve_exception(
        self, content: CoverageExceptionContent
    ) -> SignedExceptionReference:
        statement = canonical_json_bytes(content)
        envelope = self._signer.sign(statement)
        attestation = await self._attestations.record_and_verify(
            content,
            envelope,
            purpose=AttestationPurpose.EXCEPTION,
            predicate_type="https://med-rag.local/attestations/coverage-exception",
        )
        return await self._ledger.record_exception(content, attestation)

    async def _enumerate(
        self,
        attempt: JobAttempt,
        trust_root: TrustRootDefinition,
        connector,
    ) -> dict[str, Any]:
        stage = ReconciliationStage.ENUMERATE_INVENTORY
        existing = await self._ledger.stage_output(attempt.job_id, stage)
        if existing is not None:
            return existing
        await self._ledger.begin_stage(attempt.job_id, stage)
        raw = await connector.enumerate_inventory(trust_root, self._transport)
        responses: list[PreservedResponse] = []
        for response in raw.responses:
            stored = self._artifacts.put(
                response.body,
                media_type=response.media_type,
                kind=ArtifactKind.INVENTORY_RESPONSE,
            )
            await self._ledger.record_artifact(stored)
            responses.append(
                PreservedResponse(
                    requested_url=response.requested_url,
                    final_url=response.final_url,
                    status_code=response.status_code,
                    media_type=response.media_type,
                    headers=response.headers,
                    artifact_sha256=stored.sha256,
                    byte_size=stored.byte_size,
                    fetched_at=response.fetched_at,
                )
            )
        enumeration = InventoryEnumeration(
            cutoff_at=raw.cutoff_at,
            items=raw.items,
            responses=tuple(responses),
        )
        manifest_payload = {
            "schema_version": "1.0.0",
            "trust_root_id": trust_root.trust_root_id,
            "trust_root_sha256": trust_root.sha256,
            "cutoff_at": enumeration.cutoff_at.isoformat(),
            "responses": [item.model_dump(mode="json") for item in enumeration.responses],
            "items": [
                {
                    "item_id": item.item_id,
                    "fingerprint_sha256": item.fingerprint_sha256,
                }
                for item in enumeration.items
            ],
        }
        manifest = self._artifacts.put(
            canonical_json_bytes(manifest_payload),
            media_type="application/vnd.med-rag.inventory-manifest+json",
            kind=ArtifactKind.INVENTORY_MANIFEST,
        )
        await self._ledger.record_artifact(manifest)
        output = enumeration.model_copy(
            update={"inventory_artifact_sha256": manifest.sha256}
        ).model_dump(mode="json")
        await self._attest_and_complete(
            attempt.job_id,
            trust_root,
            stage,
            input_payload={"trust_root_sha256": trust_root.sha256},
            output=output,
        )
        return output

    async def _diff(
        self,
        attempt: JobAttempt,
        trust_root: TrustRootDefinition,
        enumeration_output: dict[str, Any],
    ) -> dict[str, Any]:
        stage = ReconciliationStage.DIFF_INVENTORY
        existing = await self._ledger.stage_output(attempt.job_id, stage)
        if existing is not None:
            return existing
        await self._ledger.begin_stage(attempt.job_id, stage)
        enumeration = InventoryEnumeration.model_validate(enumeration_output)
        previous = await self._ledger.previous_inventory_items(
            trust_root.trust_root_id, exclude_job_id=attempt.job_id
        )
        current = {item.item_id: item for item in enumeration.items}
        changes: list[InventoryChange] = []
        for item in enumeration.items:
            old = previous.get(item.item_id)
            change = (
                InventoryChangeKind.NEW
                if old is None
                else (
                    InventoryChangeKind.UNCHANGED
                    if old.fingerprint_sha256 == item.fingerprint_sha256
                    else InventoryChangeKind.CHANGED
                )
            )
            changes.append(
                InventoryChange(
                    item_id=item.item_id,
                    change=change,
                    previous_fingerprint_sha256=(
                        old.fingerprint_sha256 if old is not None else None
                    ),
                    current_fingerprint_sha256=item.fingerprint_sha256,
                )
            )
        for item_id, old in previous.items():
            if item_id not in current:
                changes.append(
                    InventoryChange(
                        item_id=item_id,
                        change=InventoryChangeKind.REMOVED,
                        previous_fingerprint_sha256=old.fingerprint_sha256,
                        current_fingerprint_sha256=None,
                    )
                )
        changes_tuple = tuple(sorted(changes, key=lambda item: item.item_id))
        inventory_id = await self._ledger.create_inventory(
            job_id=attempt.job_id,
            trust_root_id=trust_root.trust_root_id,
            cutoff_at=enumeration.cutoff_at,
            inventory_artifact_sha256=str(
                enumeration_output["inventory_artifact_sha256"]
            ),
            items=enumeration.items,
            changes=tuple(
                change
                for change in changes_tuple
                if change.change is not InventoryChangeKind.REMOVED
            ),
        )
        output = {
            "inventory_id": inventory_id,
            "changes": [item.model_dump(mode="json") for item in changes_tuple],
        }
        await self._attest_and_complete(
            attempt.job_id,
            trust_root,
            stage,
            input_payload={"enumeration_sha256": canonical_sha256(enumeration_output)},
            output=output,
        )
        return output

    async def _fetch(
        self,
        attempt: JobAttempt,
        trust_root: TrustRootDefinition,
        connector,
        enumeration_output: dict[str, Any],
        diff_output: dict[str, Any],
    ) -> dict[str, Any]:
        stage = ReconciliationStage.FETCH_ARTIFACTS
        existing = await self._ledger.stage_output(attempt.job_id, stage)
        if existing is not None:
            return existing
        await self._ledger.begin_stage(attempt.job_id, stage)
        enumeration = InventoryEnumeration.model_validate(enumeration_output)
        inventory_id = str(diff_output["inventory_id"])
        artifacts: list[SourceArtifactReference] = []
        exceptions: list[SignedExceptionReference] = []
        blockers: list[str] = []
        content_changed_item_ids: list[str] = []
        # In the narrative topology `asset_id == item_id`, so every enumerated item must
        # carry its own licensing policy. `license_for_asset` raises for an item the
        # trust root never licensed, which is the refusal that keeps an unlicensed
        # document from ever reaching a closure.
        narrative_topology = (
            trust_root.connector_config.get("source_topology") == NARRATIVE_ANCHORED
        )
        for item in enumeration.items:
            if narrative_topology and trust_root.asset_licensing:
                policy = trust_root.license_for_asset(item.item_id)
                if not policy.acquisition_allowed:
                    raise ValueError(
                        f"licensing policy does not permit acquiring {item.item_id}"
                    )
            previous = await self._ledger.previous_artifact(
                trust_root.trust_root_id,
                item.item_id,
                exclude_job_id=attempt.job_id,
            )
            conditional = (
                ConditionalMetadata(
                    etag=previous.etag,
                    last_modified=previous.last_modified,
                )
                if previous is not None
                else None
            )
            try:
                response = await connector.fetch_artifact(
                    trust_root, item, self._transport, conditional
                )
                if response.status_code == 304:
                    if previous is None:
                        raise ConnectorError(
                            "NOT_MODIFIED_WITHOUT_ARTIFACT",
                            "publisher returned 304 without a preserved artifact",
                        )
                    reference = SourceArtifactReference(
                        item_id=item.item_id,
                        artifact_sha256=previous.artifact_sha256,
                        byte_size=previous.byte_size,
                        media_type=previous.media_type,
                        requested_url=response.requested_url,
                        final_url=response.final_url,
                        etag=response.headers.get("etag") or previous.etag,
                        last_modified=(
                            response.headers.get("last-modified")
                            or previous.last_modified
                        ),
                        fetched_at=response.fetched_at,
                        reused_after_not_modified=True,
                    )
                else:
                    stored = self._artifacts.put(
                        response.body,
                        media_type=response.media_type,
                        kind=ArtifactKind.SOURCE,
                    )
                    await self._ledger.record_artifact(stored)
                    if (
                        previous is not None
                        and stored.sha256 != previous.artifact_sha256
                    ):
                        content_changed_item_ids.append(item.item_id)
                        await self._ledger.mark_item_content_changed(
                            inventory_id, item_id=item.item_id
                        )
                    reference = SourceArtifactReference(
                        item_id=item.item_id,
                        artifact_sha256=stored.sha256,
                        byte_size=stored.byte_size,
                        media_type=stored.media_type,
                        requested_url=response.requested_url,
                        final_url=response.final_url,
                        etag=response.headers.get("etag"),
                        last_modified=response.headers.get("last-modified"),
                        fetched_at=response.fetched_at,
                    )
                await self._ledger.include_item(
                    inventory_id,
                    item_id=item.item_id,
                    artifact_sha256=reference.artifact_sha256,
                    etag=reference.etag,
                    last_modified=reference.last_modified,
                    requested_url=reference.requested_url,
                    final_url=reference.final_url,
                    fetched_at=reference.fetched_at,
                )
                artifacts.append(reference)
            except ConnectorError as error:
                exception = await self._ledger.find_active_exception(
                    trust_root.trust_root_id,
                    item.item_id,
                    at=enumeration.cutoff_at,
                )
                if exception is not None:
                    try:
                        await self._attestations.verify_existing_reference(
                            exception.content,
                            purpose=AttestationPurpose.EXCEPTION,
                            predicate_type=(
                                "https://med-rag.local/attestations/coverage-exception"
                            ),
                            statement_sha256=exception.statement_sha256,
                            signature_sha256=exception.signature_sha256,
                            signing_key_id=exception.signing_key_id,
                            signer_identity=exception.signer_identity,
                        )
                    except AttestationVerificationError:
                        exception = None
                if exception is not None:
                    await self._ledger.except_item(
                        inventory_id, item_id=item.item_id
                    )
                    exceptions.append(exception)
                else:
                    blocker = f"{error.reason_code}:{item.item_id}"
                    blockers.append(blocker)
                    await self._ledger.block_item(
                        inventory_id, item_id=item.item_id, blocker=blocker
                    )
        output = {
            "artifacts": [item.model_dump(mode="json") for item in artifacts],
            "exceptions": [item.model_dump(mode="json") for item in exceptions],
            "content_changed_item_ids": sorted(content_changed_item_ids),
            "blockers": sorted(blockers),
        }
        await self._attest_and_complete(
            attempt.job_id,
            trust_root,
            stage,
            input_payload={"diff_sha256": canonical_sha256(diff_output)},
            output=output,
        )
        return output

    async def _reconcile_gate(
        self,
        attempt: JobAttempt,
        trust_root: TrustRootDefinition,
        enumeration_output: dict[str, Any],
        diff_output: dict[str, Any],
        fetch_output: dict[str, Any],
    ) -> dict[str, Any]:
        stage = ReconciliationStage.RECONCILE
        existing = await self._ledger.stage_output(attempt.job_id, stage)
        if existing is not None:
            return existing
        await self._ledger.begin_stage(attempt.job_id, stage)
        enumeration = InventoryEnumeration.model_validate(enumeration_output)
        inventory_id = str(diff_output["inventory_id"])
        rows = await self._ledger.inventory_rows(inventory_id)
        included = tuple(
            row.item_id
            for row in rows
            if row.disposition == ReconciliationDisposition.INCLUDED.value
        )
        excepted = tuple(
            row.item_id
            for row in rows
            if row.disposition == ReconciliationDisposition.EXCEPTED.value
        )
        blocked = tuple(
            row
            for row in rows
            if row.disposition == ReconciliationDisposition.BLOCKED.value
        )
        snapshot = ReconciliationSnapshot(
            trust_root_id=trust_root.trust_root_id,
            publisher_id=trust_root.publisher_id,
            cutoff_at=enumeration.cutoff_at,
            inventory_artifact_sha256=str(
                enumeration_output["inventory_artifact_sha256"]
            ),
            expected_item_ids=tuple(item.item_id for item in enumeration.items),
            included_item_ids=included,
            excepted_item_ids=excepted,
            complete=not blocked,
        )
        await self._ledger.set_inventory_complete(inventory_id, snapshot.complete)
        blockers = [
            f"UNACCOUNTED_INVENTORY_ITEM:{row.item_id}:{row.blocker or 'UNKNOWN'}"
            for row in blocked
        ]
        output = {
            "snapshot": snapshot.model_dump(mode="json"),
            "exceptions": fetch_output["exceptions"],
            "blockers": sorted(blockers),
        }
        await self._attest_and_complete(
            attempt.job_id,
            trust_root,
            stage,
            input_payload={"fetch_sha256": canonical_sha256(fetch_output)},
            output=output,
        )
        return output

    async def _candidate(
        self,
        attempt: JobAttempt,
        trust_root: TrustRootDefinition,
        diff_output: dict[str, Any],
        fetch_output: dict[str, Any],
        reconciliation_output: dict[str, Any],
    ) -> dict[str, Any]:
        stage = ReconciliationStage.RELEASE_CANDIDATE
        existing = await self._ledger.stage_output(attempt.job_id, stage)
        if existing is not None:
            return existing
        await self._ledger.begin_stage(attempt.job_id, stage)
        blockers = list(reconciliation_output["blockers"])
        if blockers:
            output: dict[str, Any] = {
                "release_candidate": None,
                "blockers": sorted(blockers),
            }
            await self._attest_and_complete(
                attempt.job_id,
                trust_root,
                stage,
                input_payload={
                    "reconciliation_sha256": canonical_sha256(
                        reconciliation_output
                    )
                },
                output=output,
            )
            return output

        attestations = await self._ledger.completed_stage_attestations(attempt.job_id)
        candidate_id = "RC_" + hashlib.sha256(attempt.job_id.encode("utf-8")).hexdigest()[:32]
        content = ReconciliationCandidateContent(
            candidate_id=candidate_id,
            job_id=attempt.job_id,
            created_at=max(item.verified_at for item in attestations),
            trust_root_sha256=trust_root.sha256,
            snapshot=ReconciliationSnapshot.model_validate(
                reconciliation_output["snapshot"]
            ),
            changes=self._effective_changes(diff_output, fetch_output),
            source_artifacts=tuple(
                SourceArtifactReference.model_validate(item)
                for item in fetch_output["artifacts"]
            ),
            exceptions=tuple(
                SignedExceptionReference.model_validate(item)
                for item in fetch_output["exceptions"]
            ),
            stage_attestations=attestations,
        )
        candidate = ReconciliationReleaseCandidate.seal(content)
        candidate_artifact = self._artifacts.put(
            canonical_json_bytes(candidate),
            media_type="application/vnd.med-rag.reconciliation-candidate+json",
            kind=ArtifactKind.RELEASE_CANDIDATE,
        )
        await self._ledger.record_artifact(candidate_artifact)
        output = {
            "release_candidate": candidate.model_dump(mode="json"),
            "artifact_sha256": candidate_artifact.sha256,
            "blockers": [],
        }
        attestation = await self._attest_stage(
            attempt.job_id,
            trust_root,
            stage,
            input_payload={
                "reconciliation_sha256": canonical_sha256(reconciliation_output)
            },
            output=output,
        )
        await self._ledger.create_candidate(
            job_id=attempt.job_id,
            candidate=candidate,
            artifact_sha256=candidate_artifact.sha256,
            stage_attestation_id=attestation.attestation_id,
        )
        await self._ledger.complete_stage(
            attempt.job_id,
            stage,
            input_sha256=canonical_sha256(
                {"reconciliation_sha256": canonical_sha256(reconciliation_output)}
            ),
            output_sha256=canonical_sha256(output),
            output=output,
            attestation=attestation,
        )
        return output

    async def _attest_and_complete(
        self,
        job_id: str,
        trust_root: TrustRootDefinition,
        stage: ReconciliationStage,
        *,
        input_payload: dict[str, Any],
        output: dict[str, Any],
    ) -> VerifiedAttestationReference:
        attestation = await self._attest_stage(
            job_id,
            trust_root,
            stage,
            input_payload=input_payload,
            output=output,
        )
        await self._ledger.complete_stage(
            job_id,
            stage,
            input_sha256=canonical_sha256(input_payload),
            output_sha256=canonical_sha256(output),
            output=output,
            attestation=attestation,
        )
        return attestation

    async def _attest_stage(
        self,
        job_id: str,
        trust_root: TrustRootDefinition,
        stage: ReconciliationStage,
        *,
        input_payload: dict[str, Any],
        output: dict[str, Any],
    ) -> VerifiedAttestationReference:
        content = StageAttestationContent(
            job_id=job_id,
            trust_root_id=trust_root.trust_root_id,
            trust_root_sha256=trust_root.sha256,
            connector_name=trust_root.connector_name,
            connector_version=trust_root.connector_version,
            stage=stage,
            input_sha256=canonical_sha256(input_payload),
            output_sha256=canonical_sha256(output),
            completed_at=utc_now(),
        )
        envelope = self._signer.sign(canonical_json_bytes(content))
        return await self._attestations.record_and_verify(
            content,
            envelope,
            purpose=AttestationPurpose.STAGE,
            predicate_type=(
                f"https://med-rag.local/attestations/reconciliation/{stage.value}"
            ),
        )

    async def _report(
        self, job_id: str, trust_root_id: str
    ) -> ReconciliationReport:
        state, blockers = await self._ledger.job_state(job_id)
        enumeration_output = await self._ledger.stage_output(
            job_id, ReconciliationStage.ENUMERATE_INVENTORY
        )
        diff_output = await self._ledger.stage_output(
            job_id, ReconciliationStage.DIFF_INVENTORY
        )
        fetch_output = await self._ledger.stage_output(
            job_id, ReconciliationStage.FETCH_ARTIFACTS
        )
        items = (
            InventoryEnumeration.model_validate(enumeration_output).items
            if enumeration_output
            else ()
        )
        changes = (
            self._effective_changes(diff_output, fetch_output)
            if diff_output and fetch_output
            else ()
        )
        artifacts = fetch_output.get("artifacts", []) if fetch_output else []
        exceptions = fetch_output.get("exceptions", []) if fetch_output else []
        return ReconciliationReport(
            job_id=job_id,
            trust_root_id=trust_root_id,
            state=state,
            inventory_count=len(items),
            new_count=sum(item.change is InventoryChangeKind.NEW for item in changes),
            changed_count=sum(
                item.change is InventoryChangeKind.CHANGED for item in changes
            ),
            removed_count=sum(
                item.change is InventoryChangeKind.REMOVED for item in changes
            ),
            included_count=len(artifacts),
            excepted_count=len(exceptions),
            blockers=blockers,
            release_candidate=await self._ledger.candidate(job_id),
        )

    @staticmethod
    def _effective_changes(
        diff_output: dict[str, Any], fetch_output: dict[str, Any]
    ) -> tuple[InventoryChange, ...]:
        content_changed = set(fetch_output.get("content_changed_item_ids", []))
        changes: list[InventoryChange] = []
        for raw in diff_output["changes"]:
            change = InventoryChange.model_validate(raw)
            if (
                change.item_id in content_changed
                and change.change is InventoryChangeKind.UNCHANGED
            ):
                change = change.model_copy(
                    update={"change": InventoryChangeKind.CHANGED}
                )
            changes.append(change)
        return tuple(changes)


def parse_approval_time(value: str | datetime) -> datetime:
    return datetime.fromisoformat(value) if isinstance(value, str) else value
