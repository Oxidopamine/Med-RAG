"""Signed authority composition and source-anchored evidence materialization."""

from __future__ import annotations

import hashlib

from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.evidence_extractor import (
    DAKSourceExtractor,
    EvidenceExtractionError,
)
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.materialization_repository import (
    MaterializationRepositoryConflictError,
    SQLMaterializationRepository,
)
from app.corpus_steward.materialization_schemas import (
    MATERIALIZER_NAME,
    MATERIALIZER_VERSION,
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
    SignedAuthorityBinding,
    SignedCorpusReleaseCandidate,
    StructuralMappingAttachment,
)
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.schemas import ArtifactKind, AttestationPurpose
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
    STRUCTURED_INPUT_ATTESTATION_PREDICATE,
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

        storage_keys = await self._repository.artifact_storage_keys(
            tuple(item.artifact_sha256 for item in closure.content.narrative_artifacts)
        )
        evidence: list[MaterializedEvidenceRecord] = []
        coverage: list[AssetCoverage] = []
        blockers: list[str] = []
        evidence_artifacts: dict[str, str] = {}
        stored_evidence_artifacts = []
        for resolved in closure.content.narrative_artifacts:
            narrative, definition = configured_assets[resolved.asset_id]
            license_policy = licensing_root.license_for_asset(resolved.asset_id)
            if not license_policy.evidence_materialization_allowed:
                blockers.append(f"LICENSE_PROHIBITS_MATERIALIZATION:{resolved.asset_id}")
                coverage.append(
                    AssetCoverage(
                        asset_id=resolved.asset_id,
                        source_artifact_sha256=resolved.artifact_sha256,
                        expected_source_units=1,
                        materialized_source_units=0,
                        empty_source_units=0,
                        evidence_ids=(),
                        complete=False,
                    )
                )
                continue
            raw = self._artifacts.read(storage_keys[resolved.artifact_sha256])
            if (
                hashlib.sha256(raw).hexdigest() != resolved.artifact_sha256
                or len(raw) != resolved.byte_size
            ):
                raise ValueError(f"narrative artifact integrity check failed: {resolved.asset_id}")
            try:
                extracted = self._extractor.extract(
                    raw, media_type=resolved.media_type, source_uri=resolved.final_url
                )
            except EvidenceExtractionError as error:
                blockers.append(f"{error.reason_code}:{resolved.asset_id}")
                coverage.append(
                    AssetCoverage(
                        asset_id=resolved.asset_id,
                        source_artifact_sha256=resolved.artifact_sha256,
                        expected_source_units=1,
                        materialized_source_units=0,
                        empty_source_units=0,
                        evidence_ids=(),
                        complete=False,
                    )
                )
                continue
            asset_evidence_ids: list[str] = []
            for unit in extracted.units:
                evidence_id = self._evidence_id(run_id, resolved.asset_id, unit.source_unit_id)
                record = MaterializedEvidenceRecord.seal(
                    MaterializedEvidenceContent(
                        materialization_run_id=run_id,
                        evidence_id=evidence_id,
                        authority_binding_sha256=binding.binding_sha256,
                        asset_id=resolved.asset_id,
                        source_artifact_sha256=resolved.artifact_sha256,
                        source_unit_id=unit.source_unit_id,
                        source_title=definition.title,
                        source_version_id=(
                            f"{narrative.link_id}:{narrative.version or 'UNVERSIONED'}:"
                            f"{resolved.asset_id}"
                        ),
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
                    asset_id=resolved.asset_id,
                    source_artifact_sha256=resolved.artifact_sha256,
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

        evidence_tuple = tuple(evidence)
        if not evidence_tuple:
            blockers.append("NO_MATERIALIZED_EVIDENCE")
        coverage_report = EvidenceCoverageReport(
            authority_binding_sha256=binding.binding_sha256,
            assets=tuple(coverage),
            expected_asset_ids=tuple(configured_assets),
            evidence_count=len(evidence_tuple),
            complete=(
                {item.asset_id for item in coverage} == set(configured_assets)
                and bool(evidence_tuple)
                and all(item.complete for item in coverage)
            ),
        )
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
        report = MaterializationReport.seal(
            MaterializationReportContent(
                materialization_run_id=run_id,
                reconciliation_candidate_id=candidate_id,
                authority_binding=binding,
                structural_mapping=mapping,
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
                authority_binding_artifact_sha256=binding_artifact.sha256,
                input_run_id=closure.content.input_run_id,
                corpus_candidate=corpus_candidate,
                candidate_artifact_sha256=(
                    candidate_artifact.sha256 if candidate_artifact else None
                ),
                evidence_artifact_sha256s=evidence_artifacts,
                evidence_records=evidence_tuple,
            )
        except MaterializationRepositoryConflictError:
            concurrent = await self._repository.existing(candidate_id)
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
    def _evidence_id(run_id: str, asset_id: str, unit_id: str) -> str:
        identity = f"{run_id}:{asset_id}:{unit_id}"
        return "EV_" + hashlib.sha256(identity.encode()).hexdigest()[:32]

    @staticmethod
    def _corpus_candidate_id(run_id: str) -> str:
        return "CRC_" + hashlib.sha256(run_id.encode()).hexdigest()[:32]

    @staticmethod
    def _jurisdiction(jurisdictions: tuple[str, ...]) -> str:
        return "WORLD" if "WORLD" in jurisdictions else jurisdictions[0]
