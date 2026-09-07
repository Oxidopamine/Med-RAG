"""Deterministic evidence QA and validated corpus promotion."""

from __future__ import annotations

import hashlib

from app.corpus.releases import SQLCorpusReleaseRepository
from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.evidence_extractor import DAKSourceExtractor
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.materialization_schemas import (
    MaterializedEvidenceRecord,
    SignedNarrativeAuthorityBinding,
)
from app.corpus_steward.narrative_extractor import NarrativeSourceExtractor
from app.corpus_steward.qa_classification import automated_decision_batch
from app.corpus_steward.qa_repository import (
    QACandidateContext,
    SQLQARepository,
    canonical_source_id,
    canonical_source_version_id,
)
from app.corpus_steward.qa_schemas import (
    AnchorReplayResult,
    CompositeEvidenceVerificationAttestationContent,
    CompositeMemberVerification,
    EvidenceArtifactManifestContent,
    EvidenceArtifactReference,
    EvidenceVerificationAttestationContent,
    InventoryReconciliationAttestationContent,
    QAClassificationInput,
    QAClassificationInputItem,
    QADecisionBatchContent,
    QADecisionBatchInput,
    QADisposition,
    QAQuarantineReason,
    QAResult,
    QARunState,
    ReleasePolicy,
    ReleasePolicyAttestationContent,
    ReplayCheck,
    SignedEvidenceArtifactManifest,
    SignedQADecisionBatch,
)
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.schemas import ArtifactKind, AttestationPurpose
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.schemas.corpus import (
    AttestationReference,
    AttestationStage,
    CorpusEvidenceRecord,
    CorpusReleaseBundle,
    CorpusReleaseManifest,
    CorpusReleaseManifestContent,
    CoverageException,
    EvidenceApprovalStatus,
    EvidenceVerification,
    EvidenceVerificationCheck,
    InventorySnapshot,
    ManifestEvidenceEntry,
    VerificationInvariant,
    VerificationOutcome,
    canonical_json_bytes,
    canonical_sha256,
)

EVIDENCE_MANIFEST_PREDICATE = "https://med-rag.local/attestations/qa/EVIDENCE_ARTIFACT_MANIFEST"
QA_DECISION_BATCH_PREDICATE = "https://med-rag.local/attestations/qa/QA_DECISION_BATCH"
INVENTORY_ATTESTATION_PREDICATE = (
    "https://med-rag.local/attestations/release/INVENTORY_RECONCILIATION"
)
EVIDENCE_ATTESTATION_PREDICATE = "https://med-rag.local/attestations/release/EVIDENCE_VERIFICATION"
RELEASE_POLICY_ATTESTATION_PREDICATE = "https://med-rag.local/attestations/release/RELEASE_POLICY"
# A distinct predicate, because the statement has a different shape: it names every
# member run rather than one. A verifier that fetched a composite statement expecting
# the single-run schema should fail on the predicate, not on the parse.
COMPOSITE_EVIDENCE_ATTESTATION_PREDICATE = (
    "https://med-rag.local/attestations/release/COMPOSITE_EVIDENCE_VERIFICATION"
)


class QAService:
    def __init__(
        self,
        *,
        repository: SQLQARepository,
        releases: SQLCorpusReleaseRepository,
        trust_roots: SQLTrustRootRegistry,
        attestations: SQLAttestationRepository,
        ledger: SQLReconciliationLedger,
        artifacts: ImmutableStewardArtifactStore,
        extractor: DAKSourceExtractor,
        signer: Ed25519Signer,
        narrative_extractor: NarrativeSourceExtractor | None = None,
    ) -> None:
        self._repository = repository
        self._releases = releases
        self._trust_roots = trust_roots
        self._attestations = attestations
        self._ledger = ledger
        self._artifacts = artifacts
        self._extractor = extractor
        self._narrative_extractor = narrative_extractor or NarrativeSourceExtractor()
        self._signer = signer

    async def qa(
        self,
        corpus_candidate_id: str,
        *,
        release_policy: ReleasePolicy | None = None,
        previous_release_id: str | None = None,
        promote: bool = True,
    ) -> QAResult:
        """Decide, and by default promote the result into a release.

        `promote=False` stops at a sealed decision batch. That is what every member of a
        composite release does: promotion binds evidence to a release id inside the signed
        record, so a member that promoted itself could never be re-pointed at the composite.
        The default is `True` so no existing caller changes and the single-document path is
        the same sequence of calls it has always been. See docs/qa-promotion-separation.md.
        """
        context = await self._repository.candidate_context(corpus_candidate_id)
        trust_root = await self._trust_roots.get_revision(
            context.authority_binding.content.licensing_trust_root_sha256,
            trust_root_id=context.reconciliation.content.snapshot.trust_root_id,
        )
        if self._signer.key_id not in trust_root.trusted_stage_key_ids:
            raise ValueError("QA signing key is not trusted by the source trust root")

        stored = await self._repository.existing(corpus_candidate_id)
        if stored is None:
            manifest, manifest_artifact_sha256, replays = await self._prepare(context)
            await self._repository.record_preparation(
                manifest=manifest,
                manifest_artifact_sha256=manifest_artifact_sha256,
                replays=replays,
            )
            stored = await self._repository.existing(corpus_candidate_id)
            if stored is None:
                raise RuntimeError("QA preparation was not persisted")
        replays = await self._repository.replays(stored.qa_run_id)

        if stored.state is QARunState.VALIDATED:
            if stored.bundle_artifact_sha256 is None or stored.bundle_sha256 is None:
                raise RuntimeError("validated QA run is missing its release bundle")
            bundle = self._read_bundle(stored.bundle_artifact_sha256)
            if canonical_sha256(bundle) != stored.bundle_sha256:
                raise RuntimeError("stored corpus release bundle digest is inconsistent")
            return self._result(stored, replays, bundle=bundle)

        if stored.state is QARunState.DECIDED and not promote:
            # Already decided and not being asked to promote: idempotent, like the
            # VALIDATED branch above.
            return self._result(stored, replays, bundle=None)

        classification_input = self._classification_input(
            context, stored.qa_run_id, stored.manifest, replays
        )
        materialized = {
            item.record.content.evidence_id: item.record for item in context.evidence
        }
        decisions = automated_decision_batch(
            classification_input,
            materialized,
            materialization_candidate_id=corpus_candidate_id,
            decided_at=context.candidate.content.created_at,
        )

        policy = release_policy or ReleasePolicy()
        batch = await self._seal_decisions(context, stored, replays, decisions)
        batch_artifact = self._artifacts.put(
            canonical_json_bytes(batch),
            media_type="application/vnd.med-rag.qa-decision-batch+json",
            kind=ArtifactKind.QA_DECISION_BATCH,
        )
        await self._ledger.record_artifact(batch_artifact)

        if not promote:
            await self._repository.record_decision(
                batch=batch, batch_artifact_sha256=batch_artifact.sha256
            )
            decided = await self._repository.existing(corpus_candidate_id)
            if decided is None:
                raise RuntimeError("decided QA run was not persisted")
            return self._result(decided, replays, bundle=None)

        approved, decision_digests, canonical_artifacts = self._promote(context, replays, batch)
        await self._ledger.record_artifacts(canonical_artifacts)
        release_id = self._release_id(context.candidate.candidate_sha256, batch.batch_sha256)
        collection = f"corpus_{release_id.lower()}"
        attestations = await self._release_attestations(
            context=context,
            batch=batch,
            approved=approved,
            policy=policy,
            release_id=release_id,
            qdrant_collection=collection,
        )
        bundle = self._bundle(
            context=context,
            approved=approved,
            decision_digests=decision_digests,
            policy=policy,
            release_id=release_id,
            qdrant_collection=collection,
            attestations=attestations,
            previous_release_id=previous_release_id,
            created_at=batch.content.decided_at,
        )
        if bundle.activation_blockers():
            raise RuntimeError(
                "QA produced a non-activatable release contract: "
                + ", ".join(bundle.activation_blockers())
            )
        await self._repository.register_promoted_sources(
            evidence=approved,
            authority_binding=context.authority_binding,
            trust_root=trust_root,
        )
        release_record = await self._releases.register_candidate(bundle)
        if release_record.state.value != "VALIDATED" or release_record.index_status != "NOT_BUILT":
            raise RuntimeError("QA release registration did not stop at validated/no-index state")

        bundle_bytes = canonical_json_bytes(bundle)
        bundle_artifact = self._artifacts.put(
            bundle_bytes,
            media_type="application/vnd.med-rag.corpus-release-bundle+json",
            kind=ArtifactKind.CORPUS_RELEASE_BUNDLE,
        )
        await self._ledger.record_artifact(bundle_artifact)
        bundle_sha256 = hashlib.sha256(bundle_bytes).hexdigest()
        await self._repository.record_completion(
            batch=batch,
            batch_artifact_sha256=batch_artifact.sha256,
            bundle=bundle,
            bundle_sha256=bundle_sha256,
            bundle_artifact_sha256=bundle_artifact.sha256,
        )
        completed = await self._repository.existing(corpus_candidate_id)
        if completed is None:
            raise RuntimeError("completed QA run was not persisted")
        return self._result(completed, replays, bundle=bundle)

    async def promote_decided(
        self,
        qa_run_ids: tuple[str, ...],
        *,
        release_id: str,
        qdrant_collection: str,
        release_policy: ReleasePolicy | None = None,
        previous_release_id: str | None = None,
    ) -> CorpusReleaseBundle:
        """Promote several DECIDED runs into one release.

        The composite counterpart of the tail of `qa()`. Every member has already replayed
        its anchors and sealed a complete decision batch; this binds them to one release id,
        signs attestations over the union, and registers once through the same registrar a
        single-document release goes through.

        All members must share one reconciliation candidate. In the narrative topology they
        do by construction - thirteen guidelines are thirteen *items* of one candidate - and
        requiring it is what lets the inventory attestation describe the composite honestly
        rather than describe one member and be signed as if it covered the rest.
        """

        if not qa_run_ids:
            raise ValueError("a composite release needs at least one QA run")
        members = []
        for qa_run_id in qa_run_ids:
            candidate_id = await self._repository.candidate_id_for_run(qa_run_id)
            context = await self._repository.candidate_context(candidate_id)
            stored = await self._repository.existing(candidate_id)
            if stored is None or stored.decision_batch is None:
                raise ValueError(f"QA run {qa_run_id} has no sealed decision batch")
            if stored.state is not QARunState.DECIDED:
                raise ValueError(
                    f"QA run {qa_run_id} is {stored.state.value}, not DECIDED; only a run "
                    "that decided without promoting can join a composite"
                )
            replays = await self._repository.replays(stored.qa_run_id)
            members.append((context, stored, replays))

        reconciliations = {
            context.reconciliation.candidate_sha256 for context, _, _ in members
        }
        if len(reconciliations) != 1:
            raise ValueError(
                "composed QA runs come from different reconciliation candidates; their "
                "inventory attestation could not describe all of them"
            )

        approved: list[CorpusEvidenceRecord] = []
        decision_digests: dict[str, str] = {}
        artifacts = []
        verifications = []
        per_member_approved: list[tuple[QACandidateContext, tuple[CorpusEvidenceRecord, ...]]] = []
        for context, stored, replays in members:
            batch = stored.decision_batch
            member_approved, member_digests, member_artifacts = self._promote(
                context, replays, batch, release_id=release_id
            )
            approved.extend(member_approved)
            decision_digests.update(member_digests)
            artifacts.extend(member_artifacts)
            per_member_approved.append((context, member_approved))
            verifications.append(
                CompositeMemberVerification(
                    qa_run_id=stored.qa_run_id,
                    corpus_release_candidate_id=(
                        stored.manifest.content.corpus_release_candidate_id
                    ),
                    evidence_artifact_manifest_sha256=(
                        batch.content.evidence_artifact_manifest_sha256
                    ),
                    replay_set_sha256=batch.content.replay_set_sha256,
                    decision_batch_sha256=batch.batch_sha256,
                    materialized_count=len(batch.content.decisions),
                    approved_count=len(member_approved),
                    quarantined_count=len(batch.content.decisions) - len(member_approved),
                )
            )
        collisions = len(approved) - len({item.evidence_id for item in approved})
        if collisions:
            raise ValueError(
                f"{collisions} evidence ID(s) appear in more than one composed member"
            )
        await self._ledger.record_artifacts(tuple(artifacts))
        approved_tuple = tuple(sorted(approved, key=lambda item: item.evidence_id))

        policy = release_policy or ReleasePolicy()
        primary_context, primary_stored, _ = members[0]
        attestations = await self._composite_attestations(
            context=primary_context,
            verifications=tuple(verifications),
            approved=approved_tuple,
            policy=policy,
            release_id=release_id,
            qdrant_collection=qdrant_collection,
            attested_at=primary_stored.decision_batch.content.decided_at,
        )
        bundle = self._bundle(
            context=primary_context,
            approved=approved_tuple,
            decision_digests=decision_digests,
            policy=policy,
            release_id=release_id,
            qdrant_collection=qdrant_collection,
            attestations=attestations,
            previous_release_id=previous_release_id,
            created_at=primary_stored.decision_batch.content.decided_at,
        )
        if bundle.activation_blockers():
            raise RuntimeError(
                "composite promotion produced a non-activatable release contract: "
                + ", ".join(bundle.activation_blockers())
            )
        trust_root = await self._trust_roots.get_revision(
            primary_context.authority_binding.content.licensing_trust_root_sha256,
            trust_root_id=primary_context.reconciliation.content.snapshot.trust_root_id,
        )
        # Per member, not once over the union: each member's authority binding names only
        # its own asset, and `register_promoted_sources` resolves every record's source back
        # to an asset in the binding it is given. Passing one member's binding for all of
        # them fails to resolve the others - loudly, but only because that lookup happens to
        # be strict.
        for member_context, member_approved in per_member_approved:
            await self._repository.register_promoted_sources(
                evidence=member_approved,
                authority_binding=member_context.authority_binding,
                trust_root=trust_root,
            )
        await self._releases.register_candidate(bundle)

        bundle_bytes = canonical_json_bytes(bundle)
        bundle_artifact = self._artifacts.put(
            bundle_bytes,
            media_type="application/vnd.med-rag.corpus-release-bundle+json",
            kind=ArtifactKind.CORPUS_RELEASE_BUNDLE,
        )
        await self._ledger.record_artifact(bundle_artifact)
        bundle_sha256 = hashlib.sha256(bundle_bytes).hexdigest()
        for _context, stored, _replays in members:
            await self._repository.record_completion(
                batch=stored.decision_batch,
                batch_artifact_sha256=stored.decision_batch_artifact_sha256,
                bundle=bundle,
                bundle_sha256=bundle_sha256,
                bundle_artifact_sha256=bundle_artifact.sha256,
            )
        return bundle

    async def _composite_attestations(
        self,
        *,
        context: QACandidateContext,
        verifications: tuple[CompositeMemberVerification, ...],
        approved: tuple[CorpusEvidenceRecord, ...],
        policy: ReleasePolicy,
        release_id: str,
        qdrant_collection: str,
        attested_at,
    ) -> tuple[AttestationReference, ...]:
        """The three required release attestations, signed over the composite's own content."""

        snapshot = context.reconciliation.content.snapshot
        reconcile_reference = next(
            item
            for item in context.reconciliation.content.stage_attestations
            if item.predicate_type.rsplit("/", 1)[-1] == "RECONCILE"
        )
        inventory_content = InventoryReconciliationAttestationContent(
            corpus_release_id=release_id,
            reconciliation_candidate_id=context.reconciliation.content.candidate_id,
            reconciliation_candidate_sha256=context.reconciliation.candidate_sha256,
            inventory_snapshot_sha256=canonical_sha256(snapshot),
            upstream_reconcile_attestation_id=reconcile_reference.attestation_id,
            complete=True,
            attested_at=attested_at,
        )
        inventory = await self._attestations.record_and_verify(
            inventory_content,
            self._signer.sign(canonical_json_bytes(inventory_content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=INVENTORY_ATTESTATION_PREDICATE,
        )
        verification_content = CompositeEvidenceVerificationAttestationContent(
            corpus_release_id=release_id,
            members=verifications,
            canonical_evidence_set_sha256=canonical_sha256(
                {"evidence": [(item.evidence_id, item.sha256) for item in approved]}
            ),
            materialized_count=sum(item.materialized_count for item in verifications),
            approved_count=sum(item.approved_count for item in verifications),
            quarantined_count=sum(item.quarantined_count for item in verifications),
            attested_at=attested_at,
        )
        verification = await self._attestations.record_and_verify(
            verification_content,
            self._signer.sign(canonical_json_bytes(verification_content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=COMPOSITE_EVIDENCE_ATTESTATION_PREDICATE,
        )
        policy_content = ReleasePolicyAttestationContent(
            corpus_release_id=release_id,
            release_policy_sha256=policy.sha256,
            inventory_attestation_sha256=inventory.statement_sha256,
            evidence_verification_attestation_sha256=verification.statement_sha256,
            qdrant_collection=qdrant_collection,
            release_contract_validated=True,
            retrieval_index_status="NOT_BUILT",
            attested_at=attested_at,
        )
        policy_reference = await self._attestations.record_and_verify(
            policy_content,
            self._signer.sign(canonical_json_bytes(policy_content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=RELEASE_POLICY_ATTESTATION_PREDICATE,
        )
        return (
            self._manifest_attestation(AttestationStage.INVENTORY_RECONCILIATION, inventory),
            self._manifest_attestation(AttestationStage.EVIDENCE_VERIFICATION, verification),
            self._manifest_attestation(AttestationStage.RELEASE_POLICY, policy_reference),
        )

    async def _prepare(
        self, context: QACandidateContext
    ) -> tuple[SignedEvidenceArtifactManifest, str, tuple[AnchorReplayResult, ...]]:
        created_at = context.candidate.content.created_at
        qa_run_id = self._qa_run_id(context.candidate.candidate_sha256)
        replays = self._replay(context, replayed_at=created_at)
        manifest_content = EvidenceArtifactManifestContent(
            qa_run_id=qa_run_id,
            corpus_release_candidate_id=(context.candidate.content.corpus_release_candidate_id),
            corpus_release_candidate_sha256=context.candidate.candidate_sha256,
            materialization_run_id=context.materialization_run_id,
            entries=tuple(
                EvidenceArtifactReference(
                    evidence_id=item.record.content.evidence_id,
                    materialized_evidence_sha256=item.record.evidence_sha256,
                    evidence_artifact_sha256=item.evidence_artifact_sha256,
                    source_artifact_sha256=item.record.content.source_artifact_sha256,
                    asset_id=item.asset_id,
                    source_unit_id=item.source_unit_id,
                )
                for item in context.evidence
            ),
            created_at=created_at,
        )
        attestation = await self._attestations.record_and_verify(
            manifest_content,
            self._signer.sign(canonical_json_bytes(manifest_content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=EVIDENCE_MANIFEST_PREDICATE,
        )
        manifest = SignedEvidenceArtifactManifest(
            content=manifest_content,
            manifest_sha256=canonical_sha256(manifest_content),
            attestation=attestation,
        )
        artifact = self._artifacts.put(
            canonical_json_bytes(manifest),
            media_type="application/vnd.med-rag.evidence-artifact-manifest+json",
            kind=ArtifactKind.EVIDENCE_ARTIFACT_MANIFEST,
        )
        await self._ledger.record_artifact(artifact)
        return manifest, artifact.sha256, replays

    def _replay(
        self, context: QACandidateContext, *, replayed_at
    ) -> tuple[AnchorReplayResult, ...]:
        """Re-extract the preserved bytes and require the stored records to match.

        The extractor is chosen by the run's topology, because replay only means anything
        when it runs the extractor that produced the evidence. A narrative release replayed
        with the DAK page extractor finds no `pdf:page:N:block:M` unit for any record, so
        every record fails replay and quarantines - the check would report a corrupted
        corpus on a correct one, which is worse than not running it.
        """

        extractor = (
            self._narrative_extractor
            if isinstance(context.authority_binding, SignedNarrativeAuthorityBinding)
            else self._extractor
        )
        by_source: dict[str, list] = {}
        for item in context.evidence:
            by_source.setdefault(item.record.content.source_artifact_sha256, []).append(item)
        extracted_units: dict[str, dict[str, object]] = {}
        source_errors: dict[str, str] = {}
        for source_sha256, items in by_source.items():
            first = items[0]
            source_bytes = self._artifacts.read(first.source_storage_key)
            if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
                source_errors[source_sha256] = "preserved source artifact digest mismatch"
                continue
            binding_asset = next(
                asset
                for asset in context.authority_binding.content.assets
                if asset.asset_id == first.asset_id
            )
            try:
                extracted = extractor.extract(
                    source_bytes,
                    media_type=binding_asset.media_type,
                    source_uri=binding_asset.source_uri,
                )
                extracted_units[source_sha256] = {
                    unit.source_unit_id: unit for unit in extracted.units
                }
            except Exception as error:
                source_errors[source_sha256] = str(error)

        results: list[AnchorReplayResult] = []
        for item in context.evidence:
            record = item.record
            content = record.content
            evidence_bytes = self._artifacts.read(item.evidence_storage_key)
            evidence_artifact_valid = (
                hashlib.sha256(evidence_bytes).hexdigest() == item.evidence_artifact_sha256
                and evidence_bytes == canonical_json_bytes(record)
            )
            unit = extracted_units.get(content.source_artifact_sha256, {}).get(
                content.source_unit_id
            )
            source_error = source_errors.get(content.source_artifact_sha256)
            exact = unit is not None and unit.content_exact == content.content_exact
            structure = (
                unit is not None
                and unit.content_search == content.content_search
                and unit.anchors == content.anchors
            )
            provenance = evidence_artifact_valid and source_error is None
            structural_invariant = (
                VerificationInvariant.TABLE_STRUCTURE
                if any(anchor.kind.value == "TABLE_CELL" for anchor in content.anchors)
                else VerificationInvariant.READING_ORDER
            )
            checks = (
                ReplayCheck(
                    invariant=VerificationInvariant.PROVENANCE,
                    outcome=(VerificationOutcome.PASS if provenance else VerificationOutcome.FAIL),
                    details=(
                        {}
                        if provenance
                        else {
                            "source_error": source_error,
                            "evidence_artifact_valid": evidence_artifact_valid,
                        }
                    ),
                ),
                ReplayCheck(
                    invariant=VerificationInvariant.EXACT_CONTENT,
                    outcome=(VerificationOutcome.PASS if exact else VerificationOutcome.FAIL),
                    details={} if exact else {"source_unit_found": unit is not None},
                ),
                ReplayCheck(
                    invariant=structural_invariant,
                    outcome=(VerificationOutcome.PASS if structure else VerificationOutcome.FAIL),
                    details=(
                        {}
                        if structure
                        else {
                            "source_unit_found": unit is not None,
                            "anchor_count": len(content.anchors),
                        }
                    ),
                ),
            )
            replayed_sha256 = None
            if unit is not None:
                replayed_content = content.model_copy(
                    update={
                        "content_exact": unit.content_exact,
                        "content_search": unit.content_search,
                        "anchors": unit.anchors,
                    }
                )
                replayed_sha256 = MaterializedEvidenceRecord.seal(replayed_content).evidence_sha256
            results.append(
                AnchorReplayResult(
                    evidence_id=content.evidence_id,
                    materialized_evidence_sha256=record.evidence_sha256,
                    source_artifact_sha256=content.source_artifact_sha256,
                    evidence_artifact_sha256=item.evidence_artifact_sha256,
                    replayed_evidence_sha256=replayed_sha256,
                    checks=checks,
                    passed=all(check.outcome is VerificationOutcome.PASS for check in checks),
                    replayed_at=replayed_at,
                )
            )
        return tuple(sorted(results, key=lambda item: item.evidence_id))

    async def _seal_decisions(
        self,
        context: QACandidateContext,
        stored,
        replays: tuple[AnchorReplayResult, ...],
        supplied: QADecisionBatchInput,
    ) -> SignedQADecisionBatch:
        if supplied.corpus_release_candidate_id != (
            context.candidate.content.corpus_release_candidate_id
        ):
            raise ValueError("decision batch identifies another corpus candidate")
        records = {item.record.content.evidence_id: item.record for item in context.evidence}
        decisions = {item.evidence_id: item for item in supplied.decisions}
        replay_map = {item.evidence_id: item for item in replays}
        if set(decisions) != set(records):
            missing = len(set(records) - set(decisions))
            extra = len(set(decisions) - set(records))
            raise ValueError(
                "decision batch must cover every materialized record "
                f"(missing={missing}, extra={extra})"
            )
        for evidence_id, decision in decisions.items():
            if decision.materialized_evidence_sha256 != records[evidence_id].evidence_sha256:
                raise ValueError(f"decision digest mismatch: {evidence_id}")
            replay = replay_map[evidence_id]
            if decision.disposition is QADisposition.APPROVE and not replay.passed:
                raise ValueError(f"failed anchor replay cannot be approved: {evidence_id}")
            if (
                decision.disposition is QADisposition.QUARANTINE
                and not replay.passed
                and not {
                    QAQuarantineReason.ANCHOR_REPLAY_FAILED,
                    QAQuarantineReason.EXTRACTION_STRUCTURE_FAILED,
                }
                & set(decision.quarantine_reasons)
            ):
                raise ValueError(f"quarantine must acknowledge failed anchor replay: {evidence_id}")
        approved_search = [
            records[item.evidence_id].content.content_search.casefold()
            for item in supplied.decisions
            if item.disposition is QADisposition.APPROVE
        ]
        if len(approved_search) != len(set(approved_search)):
            raise ValueError("duplicate exact search content cannot be approved twice")

        replay_set_sha256 = canonical_sha256(
            {"replays": [(item.evidence_id, item.sha256) for item in replays]}
        )
        content = QADecisionBatchContent(
            **supplied.model_dump(mode="python"),
            qa_run_id=stored.qa_run_id,
            evidence_artifact_manifest_sha256=stored.manifest.manifest_sha256,
            replay_set_sha256=replay_set_sha256,
        )
        attestation = await self._attestations.record_and_verify(
            content,
            self._signer.sign(canonical_json_bytes(content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=QA_DECISION_BATCH_PREDICATE,
        )
        return SignedQADecisionBatch(
            content=content,
            batch_sha256=canonical_sha256(content),
            attestation=attestation,
        )

    def _promote(
        self,
        context: QACandidateContext,
        replays: tuple[AnchorReplayResult, ...],
        batch: SignedQADecisionBatch,
        release_id: str | None = None,
    ) -> tuple[
        tuple[CorpusEvidenceRecord, ...],
        dict[str, str],
        tuple,
    ]:
        records = {item.record.content.evidence_id: item.record for item in context.evidence}
        replay_map = {item.evidence_id: item for item in replays}
        # A composite passes its own id in; single-document QA derives the one committed
        # to signed history. The derivation is untouched so the HIV release's identity
        # cannot move.
        release_id = release_id or self._release_id(
            context.candidate.candidate_sha256, batch.batch_sha256
        )
        promoted: list[CorpusEvidenceRecord] = []
        artifacts = []
        decision_digests = {item.evidence_id: item.sha256 for item in batch.content.decisions}
        for decision in batch.content.decisions:
            if decision.disposition is not QADisposition.APPROVE:
                continue
            materialized = records[decision.evidence_id]
            replay = replay_map[decision.evidence_id]
            checks = [
                EvidenceVerificationCheck(
                    invariant=item.invariant,
                    outcome=item.outcome,
                    verifier="corpus-steward-anchor-replay@1.0.0",
                    evidence_digest=materialized.evidence_sha256,
                    details=item.details,
                )
                for item in replay.checks
            ]
            checks.append(
                EvidenceVerificationCheck(
                    invariant=VerificationInvariant.CRITICAL_FIELDS,
                    outcome=VerificationOutcome.PASS,
                    verifier=batch.content.decision_authority,
                    evidence_digest=materialized.evidence_sha256,
                    details={
                        "qa_decision_sha256": decision.sha256,
                        "decision_batch_sha256": batch.batch_sha256,
                    },
                )
            )
            content = materialized.content
            record = CorpusEvidenceRecord(
                corpus_release_id=release_id,
                evidence_id=content.evidence_id,
                source_id=canonical_source_id(content.asset_id),
                source_version_id=canonical_source_version_id(
                    content.asset_id, content.source_version_id
                ),
                source_artifact_sha256=content.source_artifact_sha256,
                publisher_id=content.publisher_id,
                jurisdiction=content.jurisdiction,
                language=content.language,
                lifecycle_status=content.lifecycle_status,
                evidence_roles=decision.evidence_roles,
                content_exact=content.content_exact,
                content_search=content.content_search,
                anchors=content.anchors,
                applicability=decision.applicability,
                recommendation_grade=decision.recommendation_grade,
                render_allowed=content.render_allowed,
                verification=EvidenceVerification(
                    approval_status=EvidenceApprovalStatus.APPROVED,
                    checks=tuple(checks),
                ),
            )
            promoted.append(record)
            artifacts.append(
                self._artifacts.put(
                    canonical_json_bytes(record),
                    media_type="application/vnd.med-rag.canonical-evidence+json",
                    kind=ArtifactKind.CANONICAL_EVIDENCE_RECORD,
                )
            )
        if not promoted:
            raise ValueError("a corpus release requires at least one approved evidence record")
        return (
            tuple(sorted(promoted, key=lambda item: item.evidence_id)),
            decision_digests,
            tuple(artifacts),
        )

    async def _release_attestations(
        self,
        *,
        context: QACandidateContext,
        batch: SignedQADecisionBatch,
        approved: tuple[CorpusEvidenceRecord, ...],
        policy: ReleasePolicy,
        release_id: str,
        qdrant_collection: str,
    ) -> tuple[AttestationReference, ...]:
        snapshot = context.reconciliation.content.snapshot
        reconcile_reference = next(
            item
            for item in context.reconciliation.content.stage_attestations
            if item.predicate_type.rsplit("/", 1)[-1] == "RECONCILE"
        )
        inventory_content = InventoryReconciliationAttestationContent(
            corpus_release_id=release_id,
            reconciliation_candidate_id=context.reconciliation.content.candidate_id,
            reconciliation_candidate_sha256=context.reconciliation.candidate_sha256,
            inventory_snapshot_sha256=canonical_sha256(snapshot),
            upstream_reconcile_attestation_id=reconcile_reference.attestation_id,
            complete=True,
            attested_at=batch.content.decided_at,
        )
        inventory = await self._attestations.record_and_verify(
            inventory_content,
            self._signer.sign(canonical_json_bytes(inventory_content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=INVENTORY_ATTESTATION_PREDICATE,
        )
        approved_set_sha256 = canonical_sha256(
            {"evidence": [(item.evidence_id, item.sha256) for item in approved]}
        )
        verification_content = EvidenceVerificationAttestationContent(
            corpus_release_id=release_id,
            qa_run_id=batch.content.qa_run_id,
            evidence_artifact_manifest_sha256=(batch.content.evidence_artifact_manifest_sha256),
            replay_set_sha256=batch.content.replay_set_sha256,
            decision_batch_sha256=batch.batch_sha256,
            canonical_evidence_set_sha256=approved_set_sha256,
            materialized_count=len(batch.content.decisions),
            approved_count=len(approved),
            quarantined_count=len(batch.content.decisions) - len(approved),
            attested_at=batch.content.decided_at,
        )
        verification = await self._attestations.record_and_verify(
            verification_content,
            self._signer.sign(canonical_json_bytes(verification_content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=EVIDENCE_ATTESTATION_PREDICATE,
        )
        policy_content = ReleasePolicyAttestationContent(
            corpus_release_id=release_id,
            release_policy_sha256=policy.sha256,
            inventory_attestation_sha256=inventory.statement_sha256,
            evidence_verification_attestation_sha256=verification.statement_sha256,
            qdrant_collection=qdrant_collection,
            release_contract_validated=True,
            retrieval_index_status="NOT_BUILT",
            attested_at=batch.content.decided_at,
        )
        policy_reference = await self._attestations.record_and_verify(
            policy_content,
            self._signer.sign(canonical_json_bytes(policy_content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=RELEASE_POLICY_ATTESTATION_PREDICATE,
        )
        return (
            self._manifest_attestation(AttestationStage.INVENTORY_RECONCILIATION, inventory),
            self._manifest_attestation(AttestationStage.EVIDENCE_VERIFICATION, verification),
            self._manifest_attestation(AttestationStage.RELEASE_POLICY, policy_reference),
        )

    @staticmethod
    def _manifest_attestation(stage: AttestationStage, reference) -> AttestationReference:
        return AttestationReference(
            attestation_id=reference.attestation_id,
            stage=stage,
            statement_sha256=reference.statement_sha256,
            signature_sha256=reference.signature_sha256,
            signer_identity=reference.signer_identity,
        )

    @staticmethod
    def _bundle(
        *,
        context: QACandidateContext,
        approved: tuple[CorpusEvidenceRecord, ...],
        decision_digests: dict[str, str],
        policy: ReleasePolicy,
        release_id: str,
        qdrant_collection: str,
        attestations: tuple[AttestationReference, ...],
        previous_release_id: str | None,
        created_at,
    ) -> CorpusReleaseBundle:
        snapshot = context.reconciliation.content.snapshot
        inventory = InventorySnapshot.model_validate(snapshot.model_dump(mode="python"))
        exceptions = tuple(
            CoverageException(
                exception_id=item.exception_id,
                trust_root_id=item.content.trust_root_id,
                inventory_item_id=item.content.inventory_item_id,
                reason=item.content.reason,
                details=item.content.details,
                approved_by=item.signer_identity,
                approved_at=item.content.approved_at,
                statement_sha256=item.statement_sha256,
                signature_sha256=item.signature_sha256,
            )
            for item in context.reconciliation.content.exceptions
        )
        manifest = CorpusReleaseManifest.seal(
            CorpusReleaseManifestContent(
                corpus_release_id=release_id,
                created_at=created_at,
                cutoff_at=snapshot.cutoff_at,
                previous_release_id=previous_release_id,
                trust_root_registry_sha256=(
                    context.authority_binding.content.licensing_trust_root_sha256
                ),
                release_policy_sha256=policy.sha256,
                qdrant_collection=qdrant_collection,
                inventory_snapshots=(inventory,),
                exceptions=exceptions,
                evidence=tuple(
                    ManifestEvidenceEntry(
                        evidence_id=item.evidence_id,
                        source_version_id=item.source_version_id,
                        evidence_sha256=item.sha256,
                        artifact_sha256=item.sha256,
                        qa_decision_sha256=decision_digests[item.evidence_id],
                    )
                    for item in approved
                ),
                attestations=attestations,
            )
        )
        return CorpusReleaseBundle(manifest=manifest, evidence=approved)

    @staticmethod
    def _classification_input(
        context: QACandidateContext,
        qa_run_id: str,
        manifest: SignedEvidenceArtifactManifest,
        replays: tuple[AnchorReplayResult, ...],
    ) -> QAClassificationInput:
        replay_map = {item.evidence_id: item for item in replays}
        items: list[QAClassificationInputItem] = []
        for item in context.evidence:
            content = item.record.content
            replay = replay_map[content.evidence_id]
            items.append(
                QAClassificationInputItem(
                    evidence_id=content.evidence_id,
                    materialized_evidence_sha256=item.record.evidence_sha256,
                    asset_id=content.asset_id,
                    source_unit_id=content.source_unit_id,
                    replay_passed=replay.passed,
                )
            )
        return QAClassificationInput(
            qa_run_id=qa_run_id,
            corpus_release_candidate_id=(context.candidate.content.corpus_release_candidate_id),
            evidence_artifact_manifest_sha256=manifest.manifest_sha256,
            evidence_count=len(items),
            replay_passed_count=sum(item.replay_passed for item in items),
            replay_failed_count=sum(not item.replay_passed for item in items),
            items=tuple(items),
        )

    def _read_bundle(self, artifact_sha256: str) -> CorpusReleaseBundle:
        key = f"sha256/{artifact_sha256[:2]}/{artifact_sha256}.blob"
        return CorpusReleaseBundle.model_validate_json(self._artifacts.read(key))

    @staticmethod
    def _result(stored, replays, *, bundle) -> QAResult:
        return QAResult(
            state=stored.state,
            qa_run_id=stored.qa_run_id,
            corpus_release_candidate_id=(stored.manifest.content.corpus_release_candidate_id),
            evidence_artifact_manifest_sha256=stored.manifest.manifest_sha256,
            evidence_manifest_artifact_sha256=stored.manifest_artifact_sha256,
            evidence_count=stored.evidence_count,
            replay_passed_count=sum(item.passed for item in replays),
            replay_failed_count=sum(not item.passed for item in replays),
            approved_count=stored.approved_count,
            quarantined_count=stored.quarantined_count,
            decision_batch_sha256=(
                stored.decision_batch.batch_sha256 if stored.decision_batch else None
            ),
            corpus_release_bundle=bundle,
            bundle_artifact_sha256=stored.bundle_artifact_sha256,
        )

    @staticmethod
    def _qa_run_id(candidate_sha256: str) -> str:
        digest = hashlib.sha256(f"qa:1.0.0:{candidate_sha256}".encode()).hexdigest()
        return f"QA_{digest[:32]}"

    @staticmethod
    def _release_id(candidate_sha256: str, decision_batch_sha256: str) -> str:
        digest = hashlib.sha256(
            f"release:1.0.0:{candidate_sha256}:{decision_batch_sha256}".encode()
        ).hexdigest()
        return f"CR_{digest[:32]}"
