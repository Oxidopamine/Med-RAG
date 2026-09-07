"""Signed authority composition and source-anchored evidence materialization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.evidence_extractor import (
    DAKSourceExtractor,
    EvidenceExtractionError,
    ExtractedAsset,
)
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.materialization_repository import (
    MaterializationRepositoryConflictError,
    SQLMaterializationRepository,
)
from app.corpus_steward.materialization_schemas import (
    MATERIALIZER_NAME,
    MATERIALIZER_VERSION,
    NARRATIVE_MATERIALIZER_NAME,
    NARRATIVE_MATERIALIZER_VERSION,
    AnyAuthorityBinding,
    AnySourceAnalysisAttachment,
    AssetCoverage,
    AuthorityAssetBinding,
    AuthorityBindingContent,
    AuthorityRole,
    AuthorizedUse,
    CorpusReleaseCandidateContent,
    EvidenceCoverageReport,
    MaterializationReport,
    MaterializationReportContent,
    MaterializationResult,
    MaterializationState,
    MaterializedEvidenceArtifactEntry,
    MaterializedEvidenceContent,
    MaterializedEvidenceRecord,
    NarrativeAnalysisAttachment,
    NarrativeAuthorityBindingContent,
    SignedAuthorityBinding,
    SignedCorpusReleaseCandidate,
    SignedNarrativeAuthorityBinding,
    StructuralMappingAttachment,
)
from app.corpus_steward.narrative_extractor import (
    NarrativeSourceExtractor,
    unit_inventory_digest,
)
from app.corpus_steward.narrative_repository import SQLNarrativeAnalysisRepository
from app.corpus_steward.narrative_schemas import (
    NARRATIVE_PROCESSOR_NAME,
    NARRATIVE_PROCESSOR_VERSION,
    NarrativeRunState,
    NarrativeStageAttestationContent,
)
from app.corpus_steward.narrative_service import NARRATIVE_ATTESTATION_PREDICATE
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.schemas import (
    ArtifactKind,
    AttestationPurpose,
    TrustRootDefinition,
)
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import SQLStructuredInputRepository
from app.corpus_steward.structured_input_schemas import (
    STRUCTURED_INPUT_RESOLVER_NAME,
    STRUCTURED_INPUT_RESOLVER_VERSION,
    NarrativeAuthorityDefinition,
    StructuredInputAttestationContent,
    StructuredInputState,
)
from app.corpus_steward.structured_input_service import (
    NARRATIVE_ANCHORED,
    STRUCTURED_INPUT_ATTESTATION_PREDICATE,
    source_topology,
)
from app.corpus_steward.structured_repository import SQLStructuredPackageRepository
from app.corpus_steward.structured_schemas import (
    FHIR_PACKAGE_PROCESSOR_NAME,
    FHIR_PACKAGE_PROCESSOR_VERSION,
    StructuredPackageReport,
    StructuredStageAttestationContent,
)
from app.corpus_steward.structured_service import STRUCTURED_ATTESTATION_PREDICATE
from app.schemas.corpus import canonical_json_bytes, canonical_sha256
from app.schemas.domain import SourceStatus

AUTHORITY_BINDING_PREDICATE = "https://med-rag.local/attestations/materialization/AUTHORITY_BINDING"
CORPUS_CANDIDATE_PREDICATE = (
    "https://med-rag.local/attestations/materialization/CORPUS_RELEASE_CANDIDATE"
)
NARRATIVE_AUTHORITY_BINDING_PREDICATE = (
    "https://med-rag.local/attestations/materialization/NARRATIVE_AUTHORITY_BINDING"
)


class SourceExtractor(Protocol):
    """What materialization needs of an extractor, and nothing more.

    The two topologies extract with different, separately versioned extractors -
    `DAKSourceExtractor` is frozen by the HIV release's `pdf:page:N` anchors, and the
    narrative path emits block groups. Both are addressed through this shape so the shared
    tail never has to know which one it is holding.
    """

    def extract(
        self, content: bytes, *, media_type: str, source_uri: str
    ) -> ExtractedAsset: ...


@dataclass(frozen=True)
class _AssetPlan:
    """One asset to extract, with everything the evidence record needs already resolved.

    The topologies disagree about where a title and a version identifier come from - the
    DAK reads them from a controlling-narrative definition, the narrative topology from the
    inventory item that *is* the asset - so both resolve them into this shape before the
    shared tail runs.
    """

    asset_id: str
    artifact_sha256: str
    byte_size: int
    media_type: str
    source_uri: str
    title: str
    source_version_id: str
    license_policy: object


def _uncovered(plan: _AssetPlan) -> AssetCoverage:
    """Coverage for an asset that produced no evidence, so the accounting still names it."""

    return AssetCoverage(
        asset_id=plan.asset_id,
        source_artifact_sha256=plan.artifact_sha256,
        expected_source_units=1,
        materialized_source_units=0,
        empty_source_units=0,
        evidence_ids=(),
        complete=False,
    )


class MaterializationService:
    def __init__(
        self,
        *,
        trust_roots: SQLTrustRootRegistry,
        source_repository: SQLStructuredPackageRepository,
        input_repository: SQLStructuredInputRepository,
        repository: SQLMaterializationRepository,
        ledger: SQLReconciliationLedger,
        attestations: SQLAttestationRepository,
        artifacts: ImmutableStewardArtifactStore,
        extractor: DAKSourceExtractor,
        signer: Ed25519Signer,
        narrative_repository: SQLNarrativeAnalysisRepository | None = None,
        narrative_extractor: NarrativeSourceExtractor | None = None,
    ) -> None:
        self._trust_roots = trust_roots
        self._source_repository = source_repository
        self._input_repository = input_repository
        self._repository = repository
        self._ledger = ledger
        self._attestations = attestations
        self._artifacts = artifacts
        self._extractor = extractor
        self._signer = signer
        self._narrative_repository = narrative_repository
        self._narrative_extractor = narrative_extractor or NarrativeSourceExtractor()

    async def materialize(
        self, candidate_id: str, *, item_id: str | None = None
    ) -> MaterializationResult:
        source = await self._source_repository.source_context(candidate_id, item_id=item_id)
        candidate = source.candidate
        if len(candidate.content.source_artifacts) != 1:
            # _run_id hashes the candidate and the materializer, not the item, and
            # repository.existing() looks up by candidate alone. On a multi-item
            # candidate a second --item-id would return the first item's stored run as
            # this item's result. Known limit of the structured path: the derivation is
            # committed to signed history and cannot move. The narrative materializer
            # includes the item ID from its first run instead.
            raise ValueError(
                "the structured materializer derives one run per candidate; this candidate "
                "carries more than one included item"
            )
        trust_root = await self._trust_roots.get_revision(
            candidate.content.trust_root_sha256,
            trust_root_id=candidate.content.snapshot.trust_root_id,
        )
        if self._signer.key_id not in trust_root.trusted_stage_key_ids:
            raise ValueError("signing key is not trusted by this trust-root revision")
        licensing_root = trust_root
        if not licensing_root.asset_licensing:
            licensing_root = await self._trust_roots.get(trust_root.trust_root_id)
            if not licensing_root.asset_licensing:
                raise ValueError(
                    "materialization requires a registered per-asset licensing revision"
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
            raise ValueError("materialization requires a complete structured input closure")
        closure = input_run.report
        await self._verify_input_closure(source, trust_root, input_run.attestation_id)

        structured_run = await self._source_repository.existing(
            candidate_id=candidate_id,
            item_id=source.inventory_item.item_id,
            processor_name=FHIR_PACKAGE_PROCESSOR_NAME,
            processor_version=FHIR_PACKAGE_PROCESSOR_VERSION,
        )
        if structured_run is None:
            raise ValueError("materialization requires a structured package report")
        structured = structured_run.report
        await self._verify_structured_report(
            source, trust_root, structured, structured_run.attestation_id
        )
        self._require_structural_companion(structured)

        existing = await self._repository.existing(candidate_id)
        if existing is not None:
            return self._repository.as_result(existing)

        narratives = self._narrative_definitions(trust_root.connector_config)
        configured_assets = {
            asset.asset_id: (narrative, asset)
            for narrative in narratives
            for asset in narrative.assets
        }
        resolved_assets = {item.asset_id: item for item in closure.content.narrative_artifacts}
        if set(configured_assets) != set(resolved_assets):
            raise ValueError("signed input closure does not cover configured DAK assets")
        self._require_license_overlay(
            acquisition_root=trust_root,
            licensing_root=licensing_root,
            expected_asset_ids=set(configured_assets),
        )

        structured_asset_id = licensing_root.connector_config.get("structured_asset_id")
        if not isinstance(structured_asset_id, str) or not structured_asset_id:
            raise ValueError("trust root requires connector_config.structured_asset_id")
        self._require_companion_license(
            structured,
            licensing_root.license_for_asset(structured_asset_id),
        )
        run_id = self._run_id(candidate_id)
        completed_at = closure.content.completed_at
        binding_content = AuthorityBindingContent(
            materialization_run_id=run_id,
            reconciliation_candidate_id=candidate_id,
            trust_root_id=trust_root.trust_root_id,
            trust_root_sha256=trust_root.sha256,
            licensing_trust_root_sha256=licensing_root.sha256,
            input_closure_sha256=closure.report_sha256,
            structured_report_sha256=structured.report_sha256,
            controlling_source_id=narratives[0].link_id,
            structured_companion_id=structured_asset_id,
            assets=tuple(
                [
                    AuthorityAssetBinding(
                        asset_id=item.asset_id,
                        title=item.title,
                        authority_role=AuthorityRole.CONTROLLING_CLINICAL_SOURCE,
                        authorized_use=AuthorizedUse.CLINICAL_EVIDENCE,
                        source_artifact_sha256=item.artifact_sha256,
                        media_type=item.media_type,
                        source_uri=item.final_url,
                        lifecycle_status=SourceStatus.EFFECTIVE,
                        experimental=False,
                        clinical_content_promotable=True,
                        licensing=licensing_root.license_for_asset(item.asset_id),
                    )
                    for item in closure.content.narrative_artifacts
                ]
                + [
                    AuthorityAssetBinding(
                        asset_id=structured_asset_id,
                        title=source.inventory_item.title,
                        authority_role=AuthorityRole.STRUCTURED_COMPANION,
                        authorized_use=AuthorizedUse.STRUCTURAL_MAPPING_ONLY,
                        source_artifact_sha256=source.source_artifact.artifact_sha256,
                        media_type=source.source_artifact.media_type,
                        source_uri=source.source_artifact.final_url,
                        lifecycle_status=source.inventory_item.lifecycle_status,
                        experimental=bool(
                            structured.content.implementation_guide
                            and structured.content.implementation_guide.experimental
                        ),
                        clinical_content_promotable=False,
                        licensing=licensing_root.license_for_asset(structured_asset_id),
                    )
                ]
            ),
            bound_at=completed_at,
        )
        binding_attestation = await self._attestations.record_and_verify(
            binding_content,
            self._signer.sign(canonical_json_bytes(binding_content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=AUTHORITY_BINDING_PREDICATE,
        )
        binding = SignedAuthorityBinding(
            content=binding_content,
            binding_sha256=canonical_sha256(binding_content),
            attestation=binding_attestation,
        )
        binding_artifact = self._artifacts.put(
            canonical_json_bytes(binding),
            media_type="application/vnd.med-rag.authority-binding+json",
            kind=ArtifactKind.AUTHORITY_BINDING,
        )
        await self._ledger.record_artifact(binding_artifact)


        ig = structured.content.implementation_guide
        if ig is None or structured.content.resource_inventory_sha256 is None:
            raise ValueError("structured report has no attachable resource inventory")
        mapping = StructuralMappingAttachment(
            asset_id=structured_asset_id,
            structured_run_id=structured.content.structured_run_id,
            structured_report_sha256=structured.report_sha256,
            resource_inventory_sha256=structured.content.resource_inventory_sha256,
            resource_count=structured.content.resource_count,
            implementation_guide_experimental=ig.experimental,
        )
        plans = tuple(
            _AssetPlan(
                asset_id=resolved.asset_id,
                artifact_sha256=resolved.artifact_sha256,
                byte_size=resolved.byte_size,
                media_type=resolved.media_type,
                source_uri=resolved.final_url,
                title=configured_assets[resolved.asset_id][1].title,
                source_version_id=(
                    f"{configured_assets[resolved.asset_id][0].link_id}:"
                    f"{configured_assets[resolved.asset_id][0].version or 'UNVERSIONED'}:"
                    f"{resolved.asset_id}"
                ),
                license_policy=licensing_root.license_for_asset(resolved.asset_id),
            )
            for resolved in closure.content.narrative_artifacts
        )
        return await self._materialize_assets(
            run_id=run_id,
            candidate_id=candidate_id,
            trust_root=trust_root,
            binding=binding,
            binding_artifact_sha256=binding_artifact.sha256,
            attachment=mapping,
            plans=plans,
            extractor=self._extractor,
            accepted_artifact_kinds=frozenset({"NARRATIVE_SOURCE"}),
            expected_unit_inventory={},
            completed_at=completed_at,
            input_run_id=closure.content.input_run_id,
            inventory_item_id=source.inventory_item.item_id,
        )

    async def materialize_narrative(
        self, candidate_id: str, *, item_id: str | None = None
    ) -> MaterializationResult:
        """Materialize one narrative-anchored document into evidence.

        The inverse of the DAK topology: the inventory source artifact *is* the controlling
        clinical narrative, so `asset_id == item_id` and there is no structured companion.
        One run per (candidate, item) from the first run, because this is the multi-item
        topology - a WHO NCD candidate carries thirteen guidelines, and the structured
        path's one-run-per-candidate derivation is committed to signed history and cannot
        be reused here.
        """

        if self._narrative_repository is None:
            # The census is not optional on this path: materialization exists to
            # recompute its unit inventory and disagree. A service wired without it
            # could not perform that check, so it must not materialize at all.
            raise ValueError(
                "narrative materialization requires a narrative analysis repository"
            )
        source = await self._source_repository.source_context(candidate_id, item_id=item_id)
        candidate = source.candidate
        trust_root = await self._trust_roots.get_revision(
            candidate.content.trust_root_sha256,
            trust_root_id=candidate.content.snapshot.trust_root_id,
        )
        if self._signer.key_id not in trust_root.trusted_stage_key_ids:
            raise ValueError("signing key is not trusted by this trust-root revision")
        if source_topology(trust_root) != NARRATIVE_ANCHORED:
            raise ValueError(
                "narrative materialization requires a NARRATIVE_ANCHORED trust root"
            )
        licensing_root = trust_root
        if not licensing_root.asset_licensing:
            licensing_root = await self._trust_roots.get(trust_root.trust_root_id)
            if not licensing_root.asset_licensing:
                raise ValueError(
                    "materialization requires a registered per-asset licensing revision"
                )
        if self._signer.key_id not in licensing_root.trusted_stage_key_ids:
            raise ValueError("signing key is not trusted by the licensing revision")

        item = source.inventory_item
        existing = await self._repository.existing(
            candidate_id,
            item_id=item.item_id,
            materializer_name=NARRATIVE_MATERIALIZER_NAME,
            materializer_version=NARRATIVE_MATERIALIZER_VERSION,
        )
        if existing is not None:
            return self._repository.as_result(existing)

        input_run = await self._input_repository.existing(
            candidate_id=candidate_id,
            item_id=item.item_id,
            resolver_name=STRUCTURED_INPUT_RESOLVER_NAME,
            resolver_version=STRUCTURED_INPUT_RESOLVER_VERSION,
        )
        if input_run is None or input_run.state is not StructuredInputState.RESOLVED:
            raise ValueError("materialization requires a complete input closure")
        closure = input_run.report
        await self._verify_input_closure(source, trust_root, input_run.attestation_id)

        census = await self._narrative_repository.existing(
            candidate_id=candidate_id,
            item_id=item.item_id,
            processor_name=NARRATIVE_PROCESSOR_NAME,
            processor_version=NARRATIVE_PROCESSOR_VERSION,
        )
        if census is None:
            raise ValueError("narrative materialization requires a narrative source analysis")
        if census.state is not NarrativeRunState.VALIDATED:
            raise ValueError("narrative source analysis did not clear its own gate")
        await self._verify_narrative_analysis(source, trust_root, census)

        asset_id = item.item_id
        license_policy = licensing_root.license_for_asset(asset_id)
        analysis = next(
            (item for item in census.report.content.documents if item.asset_id == asset_id),
            None,
        )
        if analysis is None or analysis.unit_inventory_sha256 is None:
            raise ValueError("narrative census does not cover this asset")
        if analysis.artifact_sha256 != source.source_artifact.artifact_sha256:
            raise ValueError("narrative census describes different bytes")

        resolved = next(
            (
                entry
                for entry in closure.content.narrative_artifacts
                if entry.asset_id == asset_id
            ),
            None,
        )
        if resolved is None:
            raise ValueError("signed input closure does not carry this narrative asset")

        run_id = self._narrative_run_id(candidate_id, asset_id)
        completed_at = closure.content.completed_at
        # The source set, not the asset - the schema requires them to differ, because
        # in the DAK topology one controlling narrative set spans several files. Here
        # the set happens to hold one member, and saying so with the publisher's own
        # scope keeps the two identifiers honestly distinct.
        controlling_source_id = f"{trust_root.trust_root_id}:{asset_id}"
        binding_content = NarrativeAuthorityBindingContent(
            materialization_run_id=run_id,
            reconciliation_candidate_id=candidate_id,
            trust_root_id=trust_root.trust_root_id,
            trust_root_sha256=trust_root.sha256,
            licensing_trust_root_sha256=licensing_root.sha256,
            input_closure_sha256=closure.report_sha256,
            narrative_analysis_sha256=census.report.report_sha256,
            controlling_source_id=controlling_source_id,
            assets=(
                AuthorityAssetBinding(
                    asset_id=asset_id,
                    title=item.title,
                    authority_role=AuthorityRole.CONTROLLING_CLINICAL_SOURCE,
                    authorized_use=AuthorizedUse.CLINICAL_EVIDENCE,
                    source_artifact_sha256=resolved.artifact_sha256,
                    media_type=resolved.media_type,
                    source_uri=resolved.final_url,
                    lifecycle_status=item.lifecycle_status,
                    experimental=False,
                    clinical_content_promotable=True,
                    licensing=license_policy,
                ),
            ),
            bound_at=completed_at,
        )
        binding_attestation = await self._attestations.record_and_verify(
            binding_content,
            self._signer.sign(canonical_json_bytes(binding_content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=NARRATIVE_AUTHORITY_BINDING_PREDICATE,
        )
        binding = SignedNarrativeAuthorityBinding(
            content=binding_content,
            binding_sha256=canonical_sha256(binding_content),
            attestation=binding_attestation,
        )
        binding_artifact = self._artifacts.put(
            canonical_json_bytes(binding),
            media_type="application/vnd.med-rag.authority-binding+json",
            kind=ArtifactKind.AUTHORITY_BINDING,
        )
        await self._ledger.record_artifact(binding_artifact)

        attachment = NarrativeAnalysisAttachment(
            asset_id=asset_id,
            narrative_run_id=census.report.content.narrative_run_id,
            narrative_analysis_sha256=census.report.report_sha256,
            unit_inventory_sha256=analysis.unit_inventory_sha256,
            unit_count=analysis.unit_count,
            declared_license_id=analysis.declaration.declared_license_id,
        )
        plan = _AssetPlan(
            asset_id=asset_id,
            artifact_sha256=resolved.artifact_sha256,
            byte_size=resolved.byte_size,
            media_type=resolved.media_type,
            source_uri=resolved.final_url,
            title=item.title,
            # One artifact is both the inventory item and the clinical narrative, so the
            # version identifier is built from the item rather than from a separate
            # controlling-narrative definition that does not exist in this topology.
            source_version_id=(
                f"{controlling_source_id}:{item.version or 'UNVERSIONED'}:{asset_id}"
            ),
            license_policy=license_policy,
        )
        return await self._materialize_assets(
            run_id=run_id,
            candidate_id=candidate_id,
            trust_root=trust_root,
            binding=binding,
            binding_artifact_sha256=binding_artifact.sha256,
            attachment=attachment,
            plans=(plan,),
            extractor=self._narrative_extractor,
            # Reconciliation preserved the guideline PDF as the inventory SOURCE artifact,
            # and re-acquiring it under a second kind is refused by the ledger - the same
            # bytes must not carry two provenance stories. Widening this check is safe:
            # ArtifactKind records how bytes were acquired, while whether they may become
            # evidence is decided by the licence policy and the authority binding above.
            accepted_artifact_kinds=frozenset({"SOURCE", "NARRATIVE_SOURCE"}),
            expected_unit_inventory={asset_id: analysis.unit_inventory_sha256},
            completed_at=completed_at,
            input_run_id=closure.content.input_run_id,
            inventory_item_id=item.item_id,
        )

    async def _verify_narrative_analysis(self, source, trust_root, census) -> None:
        """Re-derive the census attestation rather than trusting the stored row.

        Same shape as `_verify_structured_report`: a stored report that binds different
        bytes, or one signed by a key this trust-root revision does not trust, must not be
        able to authorise a materialization.
        """

        content = census.report.content
        if (
            content.reconciliation_candidate_id != source.candidate.content.candidate_id
            or content.trust_root_id != trust_root.trust_root_id
            or content.trust_root_sha256 != trust_root.sha256
            or content.inventory_item_id != source.inventory_item.item_id
            or content.source_artifact_sha256 != source.source_artifact.artifact_sha256
        ):
            raise ValueError("narrative analysis binding is inconsistent")
        reference = await self._attestations.get_reference(census.attestation_id)
        if reference.signing_key_id not in trust_root.trusted_stage_key_ids:
            raise ValueError("narrative analysis key is not trusted by this trust-root revision")
        statement = NarrativeStageAttestationContent(
            narrative_run_id=content.narrative_run_id,
            reconciliation_candidate_id=content.reconciliation_candidate_id,
            trust_root_id=trust_root.trust_root_id,
            trust_root_sha256=trust_root.sha256,
            inventory_item_id=content.inventory_item_id,
            source_artifact_sha256=content.source_artifact_sha256,
            structured_input_run_id=content.structured_input_run_id,
            input_closure_sha256=content.input_closure_sha256,
            report_sha256=census.report.report_sha256,
            completed_at=content.processed_at,
        )
        await self._attestations.verify_existing_reference(
            statement,
            purpose=AttestationPurpose.STAGE,
            predicate_type=NARRATIVE_ATTESTATION_PREDICATE,
            statement_sha256=reference.statement_sha256,
            signature_sha256=reference.signature_sha256,
            signing_key_id=reference.signing_key_id,
            signer_identity=reference.signer_identity,
        )

    async def _materialize_assets(
        self,
        *,
        run_id: str,
        candidate_id: str,
        trust_root: TrustRootDefinition,
        binding: AnyAuthorityBinding,
        binding_artifact_sha256: str,
        attachment: AnySourceAnalysisAttachment,
        plans: tuple[_AssetPlan, ...],
        extractor: SourceExtractor,
        accepted_artifact_kinds: frozenset[str],
        expected_unit_inventory: dict[str, str],
        completed_at: datetime,
        input_run_id: str,
        inventory_item_id: str,
    ) -> MaterializationResult:
        """Extract, seal, sign and persist - shared by both topologies.

        The heads differ: one resolves a FHIR package and its side-channel narratives, the
        other a single document that is its own clinical authority. From the moment there
        is an authority binding and a list of assets to extract, the work is identical and
        it signs a corpus release candidate. Two paths signing that independently is how
        they quietly stop agreeing about what a release candidate means - the same argument
        that made the input closure share its seal/attest/persist tail.
        """

        storage_keys = await self._repository.artifact_storage_keys(
            tuple(plan.artifact_sha256 for plan in plans),
            accepted_kinds=accepted_artifact_kinds,
        )
        evidence: list[MaterializedEvidenceRecord] = []
        coverage: list[AssetCoverage] = []
        blockers: list[str] = []
        evidence_artifacts: dict[str, str] = {}
        stored_evidence_artifacts = []
        for plan in plans:
            license_policy = plan.license_policy
            if not license_policy.evidence_materialization_allowed:
                blockers.append(f"LICENSE_PROHIBITS_MATERIALIZATION:{plan.asset_id}")
                coverage.append(_uncovered(plan))
                continue
            raw = self._artifacts.read(storage_keys[plan.artifact_sha256])
            if (
                hashlib.sha256(raw).hexdigest() != plan.artifact_sha256
                or len(raw) != plan.byte_size
            ):
                raise ValueError(f"narrative artifact integrity check failed: {plan.asset_id}")
            try:
                extracted = extractor.extract(
                    raw, media_type=plan.media_type, source_uri=plan.source_uri
                )
            except EvidenceExtractionError as error:
                blockers.append(f"{error.reason_code}:{plan.asset_id}")
                coverage.append(_uncovered(plan))
                continue

            # The census is a prior, independently signed statement about these bytes, and
            # this is where it earns its place: recompute the unit inventory from what was
            # actually extracted and refuse to promote if it disagrees. Without the
            # comparison the narrative analysis is a rubber stamp and this chain is weaker
            # than the structured one it stands in for.
            expected_digest = expected_unit_inventory.get(plan.asset_id)
            if expected_digest is not None and (
                unit_inventory_digest(extracted.units) != expected_digest
            ):
                blockers.append(f"UNIT_INVENTORY_MISMATCH:{plan.asset_id}")
                coverage.append(_uncovered(plan))
                continue

            asset_evidence_ids: list[str] = []
            for unit in extracted.units:
                evidence_id = self._evidence_id(run_id, plan.asset_id, unit.source_unit_id)
                record = MaterializedEvidenceRecord.seal(
                    MaterializedEvidenceContent(
                        materialization_run_id=run_id,
                        evidence_id=evidence_id,
                        authority_binding_sha256=binding.binding_sha256,
                        asset_id=plan.asset_id,
                        source_artifact_sha256=plan.artifact_sha256,
                        source_unit_id=unit.source_unit_id,
                        source_title=plan.title,
                        source_version_id=plan.source_version_id,
                        publisher_id=trust_root.publisher_id,
                        jurisdiction=self._jurisdiction(trust_root.jurisdictions),
                        language=str(trust_root.connector_config.get("language", "en")),
                        lifecycle_status=SourceStatus.EFFECTIVE,
                        content_exact=unit.content_exact,
                        content_search=unit.content_search,
                        anchors=unit.anchors,
                        render_allowed=license_policy.render_allowed,
                    )
                )
                artifact = self._artifacts.put(
                    canonical_json_bytes(record),
                    media_type="application/vnd.med-rag.materialized-evidence+json",
                    kind=ArtifactKind.EVIDENCE_RECORD,
                )
                evidence_artifacts[evidence_id] = artifact.sha256
                stored_evidence_artifacts.append(artifact)
                evidence.append(record)
                asset_evidence_ids.append(evidence_id)
            coverage.append(
                AssetCoverage(
                    asset_id=plan.asset_id,
                    source_artifact_sha256=plan.artifact_sha256,
                    expected_source_units=extracted.expected_source_units,
                    materialized_source_units=len(extracted.units),
                    empty_source_units=extracted.empty_source_units,
                    evidence_ids=tuple(asset_evidence_ids),
                    complete=(
                        len(extracted.units) + extracted.empty_source_units
                        == extracted.expected_source_units
                    ),
                )
            )

        expected_asset_ids = tuple(plan.asset_id for plan in plans)
        evidence_tuple = tuple(evidence)
        if not evidence_tuple:
            blockers.append("NO_MATERIALIZED_EVIDENCE")
        coverage_report = EvidenceCoverageReport(
            authority_binding_sha256=binding.binding_sha256,
            assets=tuple(coverage),
            expected_asset_ids=expected_asset_ids,
            evidence_count=len(evidence_tuple),
            complete=(
                {item.asset_id for item in coverage} == set(expected_asset_ids)
                and bool(evidence_tuple)
                and all(item.complete for item in coverage)
            ),
        )
        evidence_entries = tuple(
            MaterializedEvidenceArtifactEntry(
                evidence_id=item.content.evidence_id,
                evidence_sha256=item.evidence_sha256,
                artifact_sha256=evidence_artifacts[item.content.evidence_id],
                source_artifact_sha256=item.content.source_artifact_sha256,
                asset_id=item.content.asset_id,
                source_unit_id=item.content.source_unit_id,
            )
            for item in evidence_tuple
        )
        await self._ledger.record_artifacts(tuple(stored_evidence_artifacts))
        narrative = isinstance(attachment, NarrativeAnalysisAttachment)
        report = MaterializationReport.seal(
            MaterializationReportContent(
                materialization_run_id=run_id,
                reconciliation_candidate_id=candidate_id,
                materializer_name=(
                    NARRATIVE_MATERIALIZER_NAME if narrative else MATERIALIZER_NAME
                ),
                materializer_version=(
                    NARRATIVE_MATERIALIZER_VERSION if narrative else MATERIALIZER_VERSION
                ),
                authority_binding=binding,
                structural_mapping=attachment,
                evidence=evidence_entries,
                coverage=coverage_report,
                completed_at=completed_at,
                ready_for_qa=coverage_report.complete and not blockers,
                blockers=tuple(blockers),
            )
        )
        report_artifact = self._artifacts.put(
            canonical_json_bytes(report),
            media_type="application/vnd.med-rag.materialization-report+json",
            kind=ArtifactKind.MATERIALIZATION_REPORT,
        )
        await self._ledger.record_artifact(report_artifact)

        corpus_candidate = None
        candidate_artifact = None
        if report.content.ready_for_qa:
            candidate_content = CorpusReleaseCandidateContent(
                corpus_release_candidate_id=self._corpus_candidate_id(run_id),
                reconciliation_candidate_id=candidate_id,
                materialization_run_id=run_id,
                trust_root_sha256=trust_root.sha256,
                materialization_report_sha256=report.report_sha256,
                authority_binding_sha256=binding.binding_sha256,
                evidence_entries=tuple(
                    (item.content.evidence_id, item.evidence_sha256) for item in evidence_tuple
                ),
                created_at=completed_at,
            )
            candidate_attestation = await self._attestations.record_and_verify(
                candidate_content,
                self._signer.sign(canonical_json_bytes(candidate_content)),
                purpose=AttestationPurpose.STAGE,
                predicate_type=CORPUS_CANDIDATE_PREDICATE,
            )
            corpus_candidate = SignedCorpusReleaseCandidate(
                content=candidate_content,
                candidate_sha256=canonical_sha256(candidate_content),
                attestation=candidate_attestation,
            )
            candidate_artifact = self._artifacts.put(
                canonical_json_bytes(corpus_candidate),
                media_type="application/vnd.med-rag.corpus-release-candidate+json",
                kind=ArtifactKind.CORPUS_RELEASE_CANDIDATE,
            )
            await self._ledger.record_artifact(candidate_artifact)

        state = (
            MaterializationState.READY_FOR_QA
            if report.content.ready_for_qa
            else MaterializationState.BLOCKED
        )
        try:
            await self._repository.record(
                state=state,
                report=report,
                report_artifact_sha256=report_artifact.sha256,
                authority_binding_artifact_sha256=binding_artifact_sha256,
                input_run_id=input_run_id,
                inventory_item_id=inventory_item_id,
                corpus_candidate=corpus_candidate,
                candidate_artifact_sha256=(
                    candidate_artifact.sha256 if candidate_artifact else None
                ),
                evidence_artifact_sha256s=evidence_artifacts,
                evidence_records=evidence_tuple,
            )
        except MaterializationRepositoryConflictError:
            concurrent = await self._repository.existing(
                candidate_id,
                item_id=inventory_item_id,
                materializer_name=report.content.materializer_name,
                materializer_version=report.content.materializer_version,
            )
            if concurrent is None or concurrent.report.report_sha256 != report.report_sha256:
                raise
            return self._repository.as_result(concurrent)
        return MaterializationResult(
            state=state,
            report=report,
            corpus_release_candidate=corpus_candidate,
            report_artifact_sha256=report_artifact.sha256,
            candidate_artifact_sha256=(candidate_artifact.sha256 if candidate_artifact else None),
        )


    async def _verify_input_closure(self, source, trust_root, attestation_id: str) -> None:
        stored = await self._input_repository.existing(
            candidate_id=source.candidate.content.candidate_id,
            item_id=source.inventory_item.item_id,
            resolver_name=STRUCTURED_INPUT_RESOLVER_NAME,
            resolver_version=STRUCTURED_INPUT_RESOLVER_VERSION,
        )
        if stored is None:
            raise ValueError("structured input closure disappeared")
        content = stored.report.content
        if (
            content.trust_root_sha256 != trust_root.sha256
            or content.source_artifact_sha256 != source.source_artifact.artifact_sha256
            or not content.complete
        ):
            raise ValueError("structured input closure binding is inconsistent")
        reference = await self._attestations.get_reference(attestation_id)
        if reference.signing_key_id not in trust_root.trusted_stage_key_ids:
            raise ValueError("structured input closure signer is not trusted")
        statement = StructuredInputAttestationContent(
            input_run_id=content.input_run_id,
            reconciliation_candidate_id=content.reconciliation_candidate_id,
            trust_root_id=content.trust_root_id,
            trust_root_sha256=content.trust_root_sha256,
            inventory_item_id=content.inventory_item_id,
            source_artifact_sha256=content.source_artifact_sha256,
            report_sha256=stored.report.report_sha256,
            completed_at=content.completed_at,
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

    async def _verify_structured_report(
        self, source, trust_root, report: StructuredPackageReport, attestation_id: str
    ) -> None:
        content = report.content
        closure = await self._input_repository.existing(
            candidate_id=content.reconciliation_candidate_id,
            item_id=content.inventory_item_id,
            resolver_name=STRUCTURED_INPUT_RESOLVER_NAME,
            resolver_version=STRUCTURED_INPUT_RESOLVER_VERSION,
        )
        if (
            content.trust_root_sha256 != trust_root.sha256
            or content.source_artifact_sha256 != source.source_artifact.artifact_sha256
            or closure is None
            or content.structured_input_run_id != closure.report.content.input_run_id
            or content.input_closure_sha256 != closure.report.report_sha256
        ):
            raise ValueError("structured report binding is inconsistent")
        reference = await self._attestations.get_reference(attestation_id)
        if reference.signing_key_id not in trust_root.trusted_stage_key_ids:
            raise ValueError("structured report signer is not trusted")
        statement = StructuredStageAttestationContent(
            structured_run_id=content.structured_run_id,
            reconciliation_candidate_id=content.reconciliation_candidate_id,
            trust_root_id=content.trust_root_id,
            trust_root_sha256=content.trust_root_sha256,
            inventory_item_id=content.inventory_item_id,
            source_artifact_sha256=content.source_artifact_sha256,
            structured_input_run_id=content.structured_input_run_id,
            input_closure_sha256=content.input_closure_sha256,
            report_sha256=report.report_sha256,
            completed_at=content.processed_at,
        )
        await self._attestations.verify_existing_reference(
            statement,
            purpose=AttestationPurpose.STAGE,
            predicate_type=STRUCTURED_ATTESTATION_PREDICATE,
            statement_sha256=reference.statement_sha256,
            signature_sha256=reference.signature_sha256,
            signing_key_id=reference.signing_key_id,
            signer_identity=reference.signer_identity,
        )

    @staticmethod
    def _require_structural_companion(report: StructuredPackageReport) -> None:
        allowed_blockers = {
            "CONTROLLING_NARRATIVE_NOT_DECLARED",
            "IMPLEMENTATION_GUIDE_EXPERIMENTAL",
            "LICENSE_DECLARATION_CONFLICT",
        }
        blockers = set(report.content.blockers)
        if blockers - allowed_blockers:
            raise ValueError(
                "structured package has blockers beyond experimental companion status: "
                + ",".join(sorted(blockers - allowed_blockers))
            )
        if report.content.resource_count < 1:
            raise ValueError("structured companion has no structural resources")

    @staticmethod
    def _require_companion_license(report: StructuredPackageReport, license_policy) -> None:
        manifest = report.content.package_manifest
        guide = report.content.implementation_guide
        if manifest is None or guide is None:
            raise ValueError("structured companion lacks license declarations")
        expected = license_policy.source_declared_license_id or license_policy.license_id
        if manifest.license != expected or guide.license != expected:
            raise ValueError("structured companion declarations differ from its per-asset license")

    @classmethod
    def _require_license_overlay(
        cls,
        *,
        acquisition_root,
        licensing_root,
        expected_asset_ids: set[str],
    ) -> None:
        if (
            licensing_root.trust_root_id != acquisition_root.trust_root_id
            or licensing_root.publisher_id != acquisition_root.publisher_id
            or licensing_root.connector_name != acquisition_root.connector_name
        ):
            raise ValueError("licensing revision changes source identity")
        structured_asset_id = licensing_root.connector_config.get("structured_asset_id")
        if not isinstance(structured_asset_id, str) or not structured_asset_id:
            raise ValueError("licensing revision lacks a structured asset ID")
        licensed_ids = {item.asset_id for item in licensing_root.asset_licensing}
        if licensed_ids != expected_asset_ids | {structured_asset_id}:
            raise ValueError("per-asset licensing does not exactly cover authority assets")
        current_assets = {
            asset.asset_id: (asset.url, asset.media_type)
            for narrative in cls._narrative_definitions(licensing_root.connector_config)
            for asset in narrative.assets
        }
        acquisition_assets = {
            asset.asset_id: (asset.url, asset.media_type)
            for narrative in cls._narrative_definitions(acquisition_root.connector_config)
            for asset in narrative.assets
        }
        if current_assets != acquisition_assets:
            raise ValueError("licensing revision changes controlling asset identity")

    @staticmethod
    def _narrative_definitions(config: dict) -> tuple[NarrativeAuthorityDefinition, ...]:
        raw = config.get("controlling_narratives")
        if not isinstance(raw, list) or not raw:
            raise ValueError("trust root requires controlling narrative definitions")
        definitions = tuple(NarrativeAuthorityDefinition.model_validate(item) for item in raw)
        if len({item.link_id for item in definitions}) != len(definitions):
            raise ValueError("controlling narrative IDs must be unique")
        if len(definitions) != 1:
            raise ValueError("Phase 3 currently requires one controlling DAK source set")
        return definitions

    @staticmethod
    def _run_id(candidate_id: str) -> str:
        identity = f"{candidate_id}:{MATERIALIZER_NAME}:{MATERIALIZER_VERSION}"
        return "MAT_" + hashlib.sha256(identity.encode()).hexdigest()[:32]

    @staticmethod
    def _narrative_run_id(candidate_id: str, item_id: str) -> str:
        """Includes the item, unlike the structured derivation.

        The structured `_run_id` hashes only the candidate and is committed to signed
        history, so it cannot be changed; this one starts correct instead of inheriting
        that defect on the topology where multi-item candidates actually occur.
        """

        identity = ":".join(
            (
                candidate_id,
                item_id,
                NARRATIVE_MATERIALIZER_NAME,
                NARRATIVE_MATERIALIZER_VERSION,
            )
        )
        return "MAT_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _evidence_id(run_id: str, asset_id: str, unit_id: str) -> str:
        identity = f"{run_id}:{asset_id}:{unit_id}"
        return "EV_" + hashlib.sha256(identity.encode()).hexdigest()[:32]

    @staticmethod
    def _corpus_candidate_id(run_id: str) -> str:
        return "CRC_" + hashlib.sha256(run_id.encode()).hexdigest()[:32]

    @staticmethod
    def _jurisdiction(jurisdictions: tuple[str, ...]) -> str:
        return "WORLD" if "WORLD" in jurisdictions else jurisdictions[0]
