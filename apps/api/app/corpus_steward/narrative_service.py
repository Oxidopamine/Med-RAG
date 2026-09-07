"""The narrative source analysis stage: a signed, stored structural census.

The peer of `structured_service`, and the reason the narrative path adds a stage rather
than making the structured report optional. Named against the four jobs the structured
report does (docs/narrative-only-materialization.md):

1. **An independent, signed statement about the interior of the acquired bytes.** The
   document was opened under a declared processor name and version, safely, and yielded an
   enumerable set of addressable units digested as `unit_inventory_sha256`.
2. **A second, independent path back to the same anchors.** The report binds
   `input_closure_sha256` and `structured_input_run_id`, which bind `source_artifact_sha256`
   and `trust_root_sha256`.
3. **A publisher-side licence cross-check.** The document's own XMP rights statement is
   compared against the operator's per-asset policy, so the operator's claim about the
   licence is checked against the source's claim.
4. A role boundary. In the DAK topology this runs between two artifacts; here one artifact
   is both the structural and the clinical authority, so what survives is the narrower
   safety property - clinical content may be promoted only from an asset the trust root
   licenses for evidence materialization.

**This stage is not the extractor.** It produces a census; the extractor produces clinical
text. Materialization must recompute the unit inventory from the bytes it extracts and
compare it against the signed `unit_inventory_sha256`. Without that comparison this report
is a rubber stamp and the chain is weaker than the one it replaces.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.narrative_analyzer import (
    NarrativeAnalysisError,
    analyse_narrative_document,
)
from app.corpus_steward.narrative_repository import (
    NarrativeRepositoryConflictError,
    SQLNarrativeAnalysisRepository,
    StoredNarrativeRun,
)
from app.corpus_steward.narrative_schemas import (
    NARRATIVE_PROCESSOR_NAME,
    NARRATIVE_PROCESSOR_VERSION,
    NarrativeAnalysisReport,
    NarrativeAnalysisReportContent,
    NarrativeAnalysisResult,
    NarrativeCheckCode,
    NarrativeCheckOutcome,
    NarrativeDocumentAnalysis,
    NarrativeRunState,
    NarrativeStageAttestationContent,
    NarrativeValidationCheck,
)
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.schemas import (
    ArtifactKind,
    AssetLicensingPolicy,
    AttestationPurpose,
    LicensedAssetKind,
    TrustRootDefinition,
)
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import (
    SQLStructuredInputRepository,
)
from app.corpus_steward.structured_input_schemas import (
    StructuredInputAttestationContent,
    StructuredInputState,
)
from app.corpus_steward.structured_input_service import (
    NARRATIVE_ANCHORED,
    STRUCTURED_INPUT_ATTESTATION_PREDICATE,
    STRUCTURED_INPUT_RESOLVER_NAME,
    STRUCTURED_INPUT_RESOLVER_VERSION,
    source_topology,
)
from app.corpus_steward.structured_repository import SQLStructuredPackageRepository
from app.schemas.corpus import canonical_json_bytes

NARRATIVE_ATTESTATION_PREDICATE = (
    "https://med-rag.local/attestations/narrative/NARRATIVE_SOURCE_ANALYSIS"
)


class NarrativeSourceAnalysisService:
    def __init__(
        self,
        *,
        trust_roots: SQLTrustRootRegistry,
        source_repository: SQLStructuredPackageRepository,
        input_repository: SQLStructuredInputRepository,
        repository: SQLNarrativeAnalysisRepository,
        ledger: SQLReconciliationLedger,
        attestations: SQLAttestationRepository,
        artifacts: ImmutableStewardArtifactStore,
        signer: Ed25519Signer,
    ) -> None:
        self._trust_roots = trust_roots
        self._source_repository = source_repository
        self._input_repository = input_repository
        self._repository = repository
        self._ledger = ledger
        self._attestations = attestations
        self._artifacts = artifacts
        self._signer = signer

    async def analyse(
        self, candidate_id: str, *, item_id: str | None = None
    ) -> NarrativeAnalysisResult:
        source = await self._source_repository.source_context(candidate_id, item_id=item_id)
        existing = await self._repository.existing(
            candidate_id=candidate_id,
            item_id=source.inventory_item.item_id,
            processor_name=NARRATIVE_PROCESSOR_NAME,
            processor_version=NARRATIVE_PROCESSOR_VERSION,
        )
        if existing is not None:
            return await self._result(existing)

        candidate = source.candidate
        trust_root = await self._trust_roots.get_revision(
            candidate.content.trust_root_sha256,
            trust_root_id=candidate.content.snapshot.trust_root_id,
        )
        if self._signer.key_id not in trust_root.trusted_stage_key_ids:
            raise ValueError("signing key is not trusted by this trust-root revision")
        # A census of a DAK publisher would be a signed statement about the wrong
        # topology: there the source artifact is a FHIR package and the narratives hang
        # off it, so `asset_id == item_id` does not hold and the report would bind an
        # asset that is not the clinical authority. Refuse rather than produce it.
        if source_topology(trust_root) != NARRATIVE_ANCHORED:
            raise ValueError(
                "narrative source analysis requires a NARRATIVE_ANCHORED trust root"
            )

        licensing_root = trust_root
        if not licensing_root.asset_licensing:
            licensing_root = await self._trust_roots.get(trust_root.trust_root_id)
            if not licensing_root.asset_licensing:
                raise ValueError(
                    "narrative analysis requires a registered per-asset licensing revision"
                )
        if self._signer.key_id not in licensing_root.trusted_stage_key_ids:
            raise ValueError("signing key is not trusted by the licensing revision")

        input_run = await self._input_repository.existing(
            candidate_id=candidate_id,
            item_id=source.inventory_item.item_id,
            resolver_name=STRUCTURED_INPUT_RESOLVER_NAME,
            resolver_version=STRUCTURED_INPUT_RESOLVER_VERSION,
        )
        if input_run is None or input_run.state is not StructuredInputState.RESOLVED:
            raise ValueError("narrative analysis requires a resolved input closure")
        await self._verify_input_closure(candidate_id, source, trust_root, input_run)

        content_bytes = self._artifacts.read(source.artifact_storage_key)
        if hashlib.sha256(content_bytes).hexdigest() != source.source_artifact.artifact_sha256:
            raise ValueError("preserved source artifact digest verification failed")
        if len(content_bytes) != source.source_artifact.byte_size:
            raise ValueError("preserved source artifact size verification failed")

        # In this topology the inventory item *is* the controlling narrative asset.
        asset_id = source.inventory_item.item_id
        license_policy = licensing_root.license_for_asset(asset_id)

        run_id = self._run_id(candidate_id, asset_id)
        # Deterministic in the immutable candidate and processor version, so a retry after
        # a crash reuses the same artifact and the same Ed25519 signature.
        processed_at = candidate.content.created_at

        try:
            analysis, safety = analyse_narrative_document(
                content_bytes,
                asset_id=asset_id,
                artifact_sha256=source.source_artifact.artifact_sha256,
                media_type=source.source_artifact.media_type,
                source_uri=source.source_artifact.final_url,
            )
        except NarrativeAnalysisError as error:
            return await self._seal(
                run_id=run_id,
                candidate_id=candidate_id,
                trust_root=trust_root,
                source=source,
                input_run=input_run,
                processed_at=processed_at,
                documents=(),
                checks=(
                    NarrativeValidationCheck(
                        code=NarrativeCheckCode.DOCUMENT_SAFETY,
                        outcome=NarrativeCheckOutcome.BLOCK,
                        summary="the document could not be opened for analysis",
                        details={"reason_code": error.reason_code},
                    ),
                ),
                blockers=(f"{error.reason_code}:{asset_id}",),
                warnings=(),
            )

        checks, blockers, warnings = self._checks(
            analysis=analysis,
            safety=safety,
            asset_id=asset_id,
            license_policy=license_policy,
        )
        return await self._seal(
            run_id=run_id,
            candidate_id=candidate_id,
            trust_root=trust_root,
            source=source,
            input_run=input_run,
            processed_at=processed_at,
            documents=(analysis,),
            checks=checks,
            blockers=blockers,
            warnings=warnings,
        )

    @staticmethod
    def _checks(
        *,
        analysis: NarrativeDocumentAnalysis,
        safety,
        asset_id: str,
        license_policy: AssetLicensingPolicy,
    ) -> tuple[tuple[NarrativeValidationCheck, ...], tuple[str, ...], tuple[str, ...]]:
        checks: list[NarrativeValidationCheck] = []
        blockers: list[str] = []
        warnings: list[str] = []

        # DOCUMENT_SAFETY - asymmetric about silence. A document that *declares*
        # encryption, embedded files or JavaScript is making a positive statement about
        # content that has no business in a guideline corpus.
        if safety.unsafe:
            blockers.extend(f"{reason}:{asset_id}" for reason in safety.unsafe)
        checks.append(
            NarrativeValidationCheck(
                code=NarrativeCheckCode.DOCUMENT_SAFETY,
                outcome=(
                    NarrativeCheckOutcome.BLOCK
                    if safety.unsafe
                    else NarrativeCheckOutcome.PASS
                ),
                summary=(
                    "the document declares content that may not enter the corpus"
                    if safety.unsafe
                    else "the document declares no unsafe content"
                ),
                details={"unsafe": list(safety.unsafe)},
            )
        )

        # DOCUMENT_IDENTITY - the census describes the bytes the closure bound, not some
        # other copy of the same publication.
        identity_ok = analysis.asset_id == asset_id and analysis.byte_size > 0
        if not identity_ok:
            blockers.append(f"DOCUMENT_IDENTITY:{asset_id}")
        checks.append(
            NarrativeValidationCheck(
                code=NarrativeCheckCode.DOCUMENT_IDENTITY,
                outcome=(
                    NarrativeCheckOutcome.PASS
                    if identity_ok
                    else NarrativeCheckOutcome.BLOCK
                ),
                summary=(
                    "the census describes the artifact the closure bound"
                    if identity_ok
                    else "the census does not describe the bound artifact"
                ),
                details={
                    "artifact_sha256": analysis.artifact_sha256,
                    "byte_size": analysis.byte_size,
                    "page_count": analysis.declaration.page_count,
                },
            )
        )

        # UNIT_COVERAGE - a document that yields no addressable unit cannot be
        # materialized into evidence, so promoting it would bind an empty asset.
        covered = analysis.unit_count > 0 and analysis.unit_inventory_sha256 is not None
        if not covered and not safety.unsafe:
            blockers.append(f"NO_ADDRESSABLE_UNITS:{asset_id}")
        checks.append(
            NarrativeValidationCheck(
                code=NarrativeCheckCode.UNIT_COVERAGE,
                outcome=(
                    NarrativeCheckOutcome.PASS if covered else NarrativeCheckOutcome.BLOCK
                ),
                summary=(
                    f"{analysis.unit_count} addressable units enumerated"
                    if covered
                    else "the document yielded no addressable unit"
                ),
                details={
                    "unit_count": analysis.unit_count,
                    "unit_inventory_sha256": analysis.unit_inventory_sha256,
                    "page_count": analysis.declaration.page_count,
                },
            )
        )

        # LICENSE_POLICY - the operator's claim checked against the source's own claim.
        # Silence is a WARN: absence of an XMP rights statement is common in published
        # PDFs and is not a contradiction. A declared licence that *disagrees* is a BLOCK,
        # because that is a genuine conflict between two claims about the same asset.
        declared = analysis.declaration.declared_license_id
        if declared is None:
            license_outcome = NarrativeCheckOutcome.WARN
            license_summary = "the document declares no licence; policy not contradicted"
            warnings.append(f"NO_DECLARED_LICENSE:{asset_id}")
        elif declared == license_policy.license_id:
            license_outcome = NarrativeCheckOutcome.PASS
            license_summary = "the document's declared licence matches operator policy"
        else:
            license_outcome = NarrativeCheckOutcome.BLOCK
            license_summary = "the document's declared licence contradicts operator policy"
            blockers.append(f"LICENSE_CONFLICT:{asset_id}")
        checks.append(
            NarrativeValidationCheck(
                code=NarrativeCheckCode.LICENSE_POLICY,
                outcome=license_outcome,
                summary=license_summary,
                details={
                    "declared_license_id": declared,
                    "policy_license_id": license_policy.license_id,
                    "declared_statement": analysis.declaration.declared_license_statement,
                },
            )
        )

        # NARRATIVE_AUTHORITY - the safety property that survives the collapse of the two
        # artifacts into one: clinical content may be promoted only from an asset the
        # trust root licenses for evidence materialization and records as a narrative
        # source.
        authorized = (
            license_policy.evidence_materialization_allowed
            and license_policy.asset_kind is LicensedAssetKind.NARRATIVE_SOURCE
        )
        if not authorized:
            blockers.append(f"NARRATIVE_AUTHORITY:{asset_id}")
        checks.append(
            NarrativeValidationCheck(
                code=NarrativeCheckCode.NARRATIVE_AUTHORITY,
                outcome=(
                    NarrativeCheckOutcome.PASS
                    if authorized
                    else NarrativeCheckOutcome.BLOCK
                ),
                summary=(
                    "the asset is licensed as a narrative source for evidence"
                    if authorized
                    else "the asset is not licensed to carry clinical evidence"
                ),
                details={
                    "asset_kind": license_policy.asset_kind.value,
                    "evidence_materialization_allowed": (
                        license_policy.evidence_materialization_allowed
                    ),
                    "render_allowed": license_policy.render_allowed,
                },
            )
        )
        return tuple(checks), tuple(sorted(set(blockers))), tuple(sorted(set(warnings)))

    async def _seal(
        self,
        *,
        run_id: str,
        candidate_id: str,
        trust_root: TrustRootDefinition,
        source,
        input_run,
        processed_at: datetime,
        documents: tuple[NarrativeDocumentAnalysis, ...],
        checks: tuple[NarrativeValidationCheck, ...],
        blockers: tuple[str, ...],
        warnings: tuple[str, ...],
    ) -> NarrativeAnalysisResult:
        blocking = any(check.outcome is NarrativeCheckOutcome.BLOCK for check in checks)
        report = NarrativeAnalysisReport.seal(
            NarrativeAnalysisReportContent(
                narrative_run_id=run_id,
                reconciliation_candidate_id=candidate_id,
                trust_root_id=trust_root.trust_root_id,
                trust_root_sha256=trust_root.sha256,
                inventory_item_id=source.inventory_item.item_id,
                source_artifact_sha256=source.source_artifact.artifact_sha256,
                structured_input_run_id=input_run.report.content.input_run_id,
                input_closure_sha256=input_run.report.report_sha256,
                processed_at=processed_at,
                documents=documents,
                unit_count_total=sum(item.unit_count for item in documents),
                checks=checks,
                promotion_eligible=not blocking and not blockers,
                blockers=blockers,
                warnings=warnings,
            )
        )
        report_artifact = self._artifacts.put(
            canonical_json_bytes(report),
            media_type="application/vnd.med-rag.narrative-analysis-report+json",
            kind=ArtifactKind.NARRATIVE_ANALYSIS_REPORT,
        )
        await self._ledger.record_artifact(report_artifact)
        statement = NarrativeStageAttestationContent(
            narrative_run_id=run_id,
            reconciliation_candidate_id=candidate_id,
            trust_root_id=trust_root.trust_root_id,
            trust_root_sha256=trust_root.sha256,
            inventory_item_id=source.inventory_item.item_id,
            source_artifact_sha256=source.source_artifact.artifact_sha256,
            structured_input_run_id=report.content.structured_input_run_id,
            input_closure_sha256=report.content.input_closure_sha256,
            report_sha256=report.report_sha256,
            completed_at=processed_at,
        )
        attestation = await self._attestations.record_and_verify(
            statement,
            self._signer.sign(canonical_json_bytes(statement)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=NARRATIVE_ATTESTATION_PREDICATE,
        )
        state = (
            NarrativeRunState.VALIDATED
            if report.content.promotion_eligible
            else NarrativeRunState.BLOCKED
        )
        try:
            await self._repository.record(
                state=state,
                report=report,
                report_artifact_sha256=report_artifact.sha256,
                attestation_id=attestation.attestation_id,
            )
        except NarrativeRepositoryConflictError:
            concurrent = await self._repository.existing(
                candidate_id=candidate_id,
                item_id=source.inventory_item.item_id,
                processor_name=NARRATIVE_PROCESSOR_NAME,
                processor_version=NARRATIVE_PROCESSOR_VERSION,
            )
            if concurrent is None or concurrent.report.report_sha256 != report.report_sha256:
                raise
            return await self._result(concurrent)
        return NarrativeAnalysisResult(
            state=state,
            report=report,
            attestation=attestation,
            report_artifact_sha256=report_artifact.sha256,
        )

    async def _verify_input_closure(
        self, candidate_id: str, source, trust_root, input_run
    ) -> None:
        """The second independent path back to the same anchors.

        The closure is re-derived and re-verified rather than trusted because it was
        stored: a closure row that binds a different candidate or artifact, or one signed
        by a key this trust-root revision does not trust, must not be able to carry a
        census.
        """

        closure_content = input_run.report.content
        if (
            closure_content.reconciliation_candidate_id != candidate_id
            or closure_content.trust_root_id != trust_root.trust_root_id
            or closure_content.trust_root_sha256 != trust_root.sha256
            or closure_content.inventory_item_id != source.inventory_item.item_id
            or closure_content.source_artifact_sha256
            != source.source_artifact.artifact_sha256
        ):
            raise ValueError("structured input closure binding is inconsistent")
        reference = await self._attestations.get_reference(input_run.attestation_id)
        if reference.signing_key_id not in trust_root.trusted_stage_key_ids:
            raise ValueError("input closure key is not trusted by this trust-root revision")
        statement = StructuredInputAttestationContent(
            input_run_id=closure_content.input_run_id,
            reconciliation_candidate_id=closure_content.reconciliation_candidate_id,
            trust_root_id=trust_root.trust_root_id,
            trust_root_sha256=trust_root.sha256,
            inventory_item_id=source.inventory_item.item_id,
            source_artifact_sha256=source.source_artifact.artifact_sha256,
            report_sha256=input_run.report.report_sha256,
            completed_at=closure_content.completed_at,
        )
        await self._attestations.verify_existing_reference(
            statement,
            purpose=AttestationPurpose.STAGE,
            predicate_type=STRUCTURED_INPUT_ATTESTATION_PREDICATE,
            statement_sha256=reference.statement_sha256,
            signature_sha256=reference.signature_sha256,
            signing_key_id=reference.signing_key_id,
            signer_identity=reference.signer_identity,
        )

    async def _result(self, existing: StoredNarrativeRun) -> NarrativeAnalysisResult:
        return NarrativeAnalysisResult(
            state=existing.state,
            report=existing.report,
            attestation=await self._attestations.get_reference(existing.attestation_id),
            report_artifact_sha256=existing.report_artifact_sha256,
        )

    @staticmethod
    def _run_id(candidate_id: str, item_id: str) -> str:
        identity = ":".join(
            (
                candidate_id,
                item_id,
                NARRATIVE_PROCESSOR_NAME,
                NARRATIVE_PROCESSOR_VERSION,
            )
        )
        return "NAR_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
