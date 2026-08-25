"""Fail-closed validation and inventorying of reconciled FHIR source packages."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.fhir_package import (
    FHIRPackageParser,
    FHIRPackageValidationError,
    ParsedFHIRPackage,
)
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.schemas import ArtifactKind, AttestationPurpose, TrustRootDefinition
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import (
    SQLStructuredInputRepository,
)
from app.corpus_steward.structured_input_schemas import (
    STRUCTURED_INPUT_RESOLVER_NAME,
    STRUCTURED_INPUT_RESOLVER_VERSION,
    StructuredInputAttestationContent,
    StructuredInputCheckCode,
    StructuredInputCheckOutcome,
    StructuredInputClosureReport,
)
from app.corpus_steward.structured_input_service import (
    STRUCTURED_INPUT_ATTESTATION_PREDICATE,
)
from app.corpus_steward.structured_repository import (
    SQLStructuredPackageRepository,
    StoredStructuredRun,
    StructuredRepositoryConflictError,
)
from app.corpus_steward.structured_schemas import (
    FHIR_PACKAGE_PROCESSOR_NAME,
    FHIR_PACKAGE_PROCESSOR_VERSION,
    NarrativeAuthorityLink,
    NarrativeLinkStatus,
    StructuredCheckCode,
    StructuredCheckOutcome,
    StructuredPackageReport,
    StructuredPackageReportContent,
    StructuredProcessingResult,
    StructuredRunState,
    StructuredStageAttestationContent,
    StructuredValidationCheck,
)
from app.schemas.corpus import canonical_json_bytes
from app.schemas.domain import SourceStatus

STRUCTURED_ATTESTATION_PREDICATE = (
    "https://med-rag.local/attestations/structured/FHIR_PACKAGE_VALIDATION"
)


@dataclass(frozen=True)
class StructuredFHIRPolicy:
    allowed_fhir_versions: frozenset[str]
    allowed_publishers: frozenset[str]
    allow_experimental: bool
    require_declared_narrative_link: bool
    require_resolved_dependencies: bool


class StructuredPackageService:
    def __init__(
        self,
        *,
        trust_roots: SQLTrustRootRegistry,
        repository: SQLStructuredPackageRepository,
        input_repository: SQLStructuredInputRepository,
        ledger: SQLReconciliationLedger,
        attestations: SQLAttestationRepository,
        artifacts: ImmutableStewardArtifactStore,
        parser: FHIRPackageParser,
        signer: Ed25519Signer,
    ) -> None:
        self._trust_roots = trust_roots
        self._repository = repository
        self._input_repository = input_repository
        self._ledger = ledger
        self._attestations = attestations
        self._artifacts = artifacts
        self._parser = parser
        self._signer = signer

    async def process(
        self, candidate_id: str, *, item_id: str | None = None
    ) -> StructuredProcessingResult:
        source = await self._repository.source_context(candidate_id, item_id=item_id)
        existing = await self._repository.existing(
            candidate_id=candidate_id,
            item_id=source.inventory_item.item_id,
            processor_name=FHIR_PACKAGE_PROCESSOR_NAME,
            processor_version=FHIR_PACKAGE_PROCESSOR_VERSION,
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
        policy = self._policy(trust_root)
        configured_narratives = self._configured_narratives(trust_root)
        input_closure = await self._input_repository.existing(
            candidate_id=candidate_id,
            item_id=source.inventory_item.item_id,
            resolver_name=STRUCTURED_INPUT_RESOLVER_NAME,
            resolver_version=STRUCTURED_INPUT_RESOLVER_VERSION,
        )
        if input_closure is not None:
            closure_content = input_closure.report.content
            if (
                closure_content.reconciliation_candidate_id != candidate_id
                or closure_content.trust_root_id != trust_root.trust_root_id
                or closure_content.trust_root_sha256 != trust_root.sha256
                or closure_content.inventory_item_id != source.inventory_item.item_id
                or closure_content.source_artifact_sha256
                != source.source_artifact.artifact_sha256
            ):
                raise ValueError("structured input closure binding is inconsistent")
            closure_attestation = await self._attestations.get_reference(
                input_closure.attestation_id
            )
            if closure_attestation.signing_key_id not in trust_root.trusted_stage_key_ids:
                raise ValueError("structured input closure key is not trusted by the root")
            closure_statement = StructuredInputAttestationContent(
                input_run_id=closure_content.input_run_id,
                reconciliation_candidate_id=candidate_id,
                trust_root_id=trust_root.trust_root_id,
                trust_root_sha256=trust_root.sha256,
                inventory_item_id=source.inventory_item.item_id,
                source_artifact_sha256=source.source_artifact.artifact_sha256,
                report_sha256=input_closure.report.report_sha256,
                completed_at=closure_content.completed_at,
            )
            await self._attestations.verify_existing_reference(
                closure_statement,
                purpose=AttestationPurpose.STAGE,
                predicate_type=STRUCTURED_INPUT_ATTESTATION_PREDICATE,
                statement_sha256=closure_attestation.statement_sha256,
                signature_sha256=closure_attestation.signature_sha256,
                signing_key_id=closure_attestation.signing_key_id,
                signer_identity=closure_attestation.signer_identity,
            )
        content = self._artifacts.read(source.artifact_storage_key)
        if hashlib.sha256(content).hexdigest() != source.source_artifact.artifact_sha256:
            raise ValueError("preserved source artifact digest verification failed")
        if len(content) != source.source_artifact.byte_size:
            raise ValueError("preserved source artifact size verification failed")

        run_id = self._run_id(candidate_id, source.inventory_item.item_id)
        # The signed report is a deterministic function of the immutable candidate and
        # processor version, including its logical evaluation timestamp. This lets a
        # retry after a crash reuse the same artifact and deterministic Ed25519 signature.
        processed_at = candidate.content.created_at
        try:
            parsed = self._parser.parse(content)
            checks, narratives, blockers, warnings = self._checks(
                parsed=parsed,
                trust_root=trust_root,
                policy=policy,
                configured_narratives=configured_narratives,
                inventory_item=source.inventory_item,
                input_closure=(
                    input_closure.report if input_closure is not None else None
                ),
            )
            resources = parsed.resources
            report_content = StructuredPackageReportContent(
                structured_run_id=run_id,
                reconciliation_candidate_id=candidate_id,
                trust_root_id=trust_root.trust_root_id,
                trust_root_sha256=trust_root.sha256,
                inventory_item_id=source.inventory_item.item_id,
                source_artifact_sha256=source.source_artifact.artifact_sha256,
                structured_input_run_id=(
                    input_closure.report.content.input_run_id
                    if input_closure is not None
                    else None
                ),
                input_closure_sha256=(
                    input_closure.report.report_sha256
                    if input_closure is not None
                    else None
                ),
                processed_at=processed_at,
                package_manifest=parsed.manifest,
                implementation_guide=parsed.implementation_guide,
                resource_inventory_sha256=parsed.resource_inventory_sha256,
                resource_count=len(resources),
                example_count=sum(item.is_example for item in resources),
                narrative_count=sum(
                    item.narrative_sha256 is not None for item in resources
                ),
                resolved_dependency_count=(
                    len(input_closure.report.content.dependency_packages)
                    if input_closure is not None
                    else 0
                ),
                narrative_artifact_count=(
                    len(input_closure.report.content.narrative_artifacts)
                    if input_closure is not None
                    else 0
                ),
                resource_type_counts=parsed.resource_type_counts,
                narrative_authorities=narratives,
                checks=checks,
                promotion_eligible=not blockers,
                blockers=blockers,
                warnings=warnings,
            )
        except FHIRPackageValidationError as error:
            resources = ()
            narratives = ()
            blocker = f"{error.reason_code}:{source.inventory_item.item_id}"
            report_content = StructuredPackageReportContent(
                structured_run_id=run_id,
                reconciliation_candidate_id=candidate_id,
                trust_root_id=trust_root.trust_root_id,
                trust_root_sha256=trust_root.sha256,
                inventory_item_id=source.inventory_item.item_id,
                source_artifact_sha256=source.source_artifact.artifact_sha256,
                structured_input_run_id=(
                    input_closure.report.content.input_run_id
                    if input_closure is not None
                    else None
                ),
                input_closure_sha256=(
                    input_closure.report.report_sha256
                    if input_closure is not None
                    else None
                ),
                processed_at=processed_at,
                resource_count=0,
                example_count=0,
                narrative_count=0,
                checks=(
                    StructuredValidationCheck(
                        code=StructuredCheckCode.ARCHIVE_SAFETY,
                        outcome=StructuredCheckOutcome.BLOCK,
                        details=str(error),
                        diagnostics={"reason_code": error.reason_code},
                    ),
                ),
                promotion_eligible=False,
                blockers=(blocker,),
            )

        report = StructuredPackageReport.seal(report_content)
        report_artifact = self._artifacts.put(
            canonical_json_bytes(report),
            media_type="application/vnd.med-rag.structured-package-report+json",
            kind=ArtifactKind.STRUCTURED_REPORT,
        )
        await self._ledger.record_artifact(report_artifact)
        statement = StructuredStageAttestationContent(
            structured_run_id=run_id,
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
            predicate_type=STRUCTURED_ATTESTATION_PREDICATE,
        )
        state = (
            StructuredRunState.VALIDATED
            if report.content.promotion_eligible
            else StructuredRunState.BLOCKED
        )
        try:
            await self._repository.record(
                state=state,
                report=report,
                report_artifact_sha256=report_artifact.sha256,
                attestation_id=attestation.attestation_id,
                resources=resources,
                narrative_links=narratives,
            )
        except StructuredRepositoryConflictError:
            concurrent = await self._repository.existing(
                candidate_id=candidate_id,
                item_id=source.inventory_item.item_id,
                processor_name=FHIR_PACKAGE_PROCESSOR_NAME,
                processor_version=FHIR_PACKAGE_PROCESSOR_VERSION,
            )
            if concurrent is None or concurrent.report.report_sha256 != report.report_sha256:
                raise
            return await self._result(concurrent)
        return StructuredProcessingResult(
            state=state,
            report=report,
            attestation=attestation,
            report_artifact_sha256=report_artifact.sha256,
        )

    async def _result(
        self, existing: StoredStructuredRun
    ) -> StructuredProcessingResult:
        return StructuredProcessingResult(
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
                FHIR_PACKAGE_PROCESSOR_NAME,
                FHIR_PACKAGE_PROCESSOR_VERSION,
            )
        )
        return "SPR_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _policy(trust_root: TrustRootDefinition) -> StructuredFHIRPolicy:
        raw = trust_root.connector_config.get("structured_policy")
        if not isinstance(raw, dict):
            raise ValueError("trust root requires a structured_policy object")
        allowed = raw.get("allowed_fhir_versions")
        if (
            not isinstance(allowed, list)
            or not allowed
            or not all(isinstance(item, str) and item for item in allowed)
        ):
            raise ValueError("structured_policy.allowed_fhir_versions is invalid")
        allowed_publishers = raw.get("allowed_publishers")
        if (
            not isinstance(allowed_publishers, list)
            or not allowed_publishers
            or not all(
                isinstance(item, str) and item for item in allowed_publishers
            )
        ):
            raise ValueError("structured_policy.allowed_publishers is invalid")
        boolean_fields = (
            "allow_experimental",
            "require_declared_narrative_link",
            "require_resolved_dependencies",
        )
        if any(not isinstance(raw.get(field), bool) for field in boolean_fields):
            raise ValueError("structured_policy boolean gates must be explicit")
        return StructuredFHIRPolicy(
            allowed_fhir_versions=frozenset(allowed),
            allowed_publishers=frozenset(allowed_publishers),
            allow_experimental=raw["allow_experimental"],
            require_declared_narrative_link=raw[
                "require_declared_narrative_link"
            ],
            require_resolved_dependencies=raw["require_resolved_dependencies"],
        )

    @staticmethod
    def _configured_narratives(
        trust_root: TrustRootDefinition,
    ) -> tuple[dict[str, str | None], ...]:
        raw = trust_root.connector_config.get("controlling_narratives")
        if not isinstance(raw, list) or not raw:
            raise ValueError("trust root requires at least one controlling narrative")
        result: list[dict[str, str | None]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("controlling narratives must be objects")
            required = ("link_id", "title", "url")
            if any(not isinstance(item.get(key), str) or not item[key] for key in required):
                raise ValueError("controlling narrative is missing required strings")
            link_id = item["link_id"]
            if link_id in seen:
                raise ValueError("controlling narrative IDs must be unique")
            seen.add(link_id)
            optional: dict[str, str | None] = {}
            for key in ("identifier", "version"):
                value = item.get(key)
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"controlling narrative {key} must be a string")
                optional[key] = value
            result.append(
                {
                    "link_id": link_id,
                    "title": item["title"],
                    "url": item["url"],
                    **optional,
                }
            )
        return tuple(sorted(result, key=lambda item: str(item["link_id"])))

    @classmethod
    def _checks(
        cls,
        *,
        parsed: ParsedFHIRPackage,
        trust_root: TrustRootDefinition,
        policy: StructuredFHIRPolicy,
        configured_narratives: tuple[dict[str, str | None], ...],
        inventory_item,
        input_closure: StructuredInputClosureReport | None,
    ) -> tuple[
        tuple[StructuredValidationCheck, ...],
        tuple[NarrativeAuthorityLink, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        checks: list[StructuredValidationCheck] = [
            cls._check(
                StructuredCheckCode.ARCHIVE_SAFETY,
                StructuredCheckOutcome.PASS,
                "Archive members passed bounded non-extracting validation.",
            )
        ]
        blockers: list[str] = []
        warnings: list[str] = []
        manifest_ok = (
            inventory_item.item_id
            == f"{parsed.manifest.package_id}#{parsed.manifest.version}"
            and inventory_item.version == parsed.manifest.version
            and parsed.manifest.package_type == "IG"
        )
        cls._gate(
            checks,
            blockers,
            code=StructuredCheckCode.PACKAGE_MANIFEST,
            passed=manifest_ok,
            blocker="PACKAGE_MANIFEST_INVENTORY_MISMATCH",
            pass_details="Package identity and version match the reconciled inventory item.",
            block_details="Package identity, version, or type differs from inventory metadata.",
            diagnostics={
                "inventory_item_id": inventory_item.item_id,
                "package_id": parsed.manifest.package_id,
                "package_version": parsed.manifest.version,
                "package_type": parsed.manifest.package_type,
            },
        )
        ig = parsed.implementation_guide
        expected_ig_canonical = (
            f"{parsed.manifest.canonical.rstrip('/')}/"
            f"ImplementationGuide/{ig.resource_id}"
        )
        ig_ok = (
            ig.package_id == parsed.manifest.package_id
            and ig.version == parsed.manifest.version
            and ig.canonical_url == expected_ig_canonical
            and set(ig.fhir_versions) == set(parsed.manifest.fhir_versions)
            and ig.publisher in policy.allowed_publishers
        )
        ig_blocker = (
            "IMPLEMENTATION_GUIDE_PUBLISHER_NOT_ALLOWED"
            if ig.publisher not in policy.allowed_publishers
            else "IMPLEMENTATION_GUIDE_MANIFEST_MISMATCH"
        )
        cls._gate(
            checks,
            blockers,
            code=StructuredCheckCode.IMPLEMENTATION_GUIDE_BINDING,
            passed=ig_ok,
            blocker=ig_blocker,
            pass_details="ImplementationGuide identity is bound to the package manifest.",
            block_details="ImplementationGuide identity differs from the package manifest.",
            diagnostics={
                "implementation_guide_canonical": ig.canonical_url,
                "expected_canonical": expected_ig_canonical,
                "publisher": ig.publisher,
                "allowed_publishers": sorted(policy.allowed_publishers),
            },
        )
        checks.append(
            cls._check(
                StructuredCheckCode.RESOURCE_IDENTITY,
                StructuredCheckOutcome.PASS,
                "Every indexed FHIR JSON member has a unique filename-bound identity.",
                {"resource_count": len(parsed.resources)},
            )
        )
        coverage_ok = not (
            parsed.missing_declared_resources or parsed.undeclared_resources
        )
        cls._gate(
            checks,
            blockers,
            code=StructuredCheckCode.DECLARED_RESOURCE_COVERAGE,
            passed=coverage_ok,
            blocker="IMPLEMENTATION_GUIDE_RESOURCE_COVERAGE_INCOMPLETE",
            pass_details="ImplementationGuide declarations exactly cover package resources.",
            block_details="Declared and physically present package resources differ.",
            diagnostics={
                "missing": list(parsed.missing_declared_resources),
                "undeclared": list(parsed.undeclared_resources),
            },
        )
        without_narrative = sum(
            not item.is_example and item.narrative_sha256 is None
            for item in parsed.resources
        )
        if without_narrative:
            warnings.append(f"FHIR_RESOURCES_WITHOUT_NARRATIVE:{without_narrative}")
        checks.append(
            cls._check(
                StructuredCheckCode.RESOURCE_NARRATIVE_COVERAGE,
                (
                    StructuredCheckOutcome.WARN
                    if without_narrative
                    else StructuredCheckOutcome.PASS
                ),
                (
                    "Some non-example resources do not contain generated narrative."
                    if without_narrative
                    else "Every non-example resource contains generated narrative."
                ),
                {"without_narrative_count": without_narrative},
            )
        )
        versions_ok = bool(parsed.manifest.fhir_versions) and set(
            parsed.manifest.fhir_versions
        ).issubset(policy.allowed_fhir_versions)
        cls._gate(
            checks,
            blockers,
            code=StructuredCheckCode.FHIR_VERSION_POLICY,
            passed=versions_ok,
            blocker="FHIR_VERSION_NOT_ALLOWED",
            pass_details="Package FHIR versions are explicitly allowed by the trust root.",
            block_details="Package declares a FHIR version outside trust-root policy.",
            diagnostics={
                "declared": list(parsed.manifest.fhir_versions),
                "allowed": sorted(policy.allowed_fhir_versions),
            },
        )
        lifecycle_ok = (
            inventory_item.lifecycle_status
            in {SourceStatus.APPROVED, SourceStatus.EFFECTIVE}
            and ig.status == "active"
            and (policy.allow_experimental or not ig.experimental)
        )
        lifecycle_blocker = (
            "IMPLEMENTATION_GUIDE_EXPERIMENTAL"
            if ig.experimental and not policy.allow_experimental
            else "IMPLEMENTATION_GUIDE_LIFECYCLE_NOT_ALLOWED"
        )
        cls._gate(
            checks,
            blockers,
            code=StructuredCheckCode.LIFECYCLE_POLICY,
            passed=lifecycle_ok,
            blocker=lifecycle_blocker,
            pass_details="Inventory and ImplementationGuide lifecycle states are eligible.",
            block_details="Lifecycle or experimental status is not eligible for promotion.",
            diagnostics={
                "inventory_status": inventory_item.lifecycle_status.value,
                "implementation_guide_status": ig.status,
                "experimental": ig.experimental,
                "allow_experimental": policy.allow_experimental,
            },
        )
        if trust_root.asset_licensing:
            structured_asset_id = trust_root.connector_config.get("structured_asset_id")
            if not isinstance(structured_asset_id, str) or not structured_asset_id:
                raise ValueError(
                    "per-asset licensing requires connector_config.structured_asset_id"
                )
            asset_license = trust_root.license_for_asset(structured_asset_id)
            expected_license = (
                asset_license.source_declared_license_id or asset_license.license_id
            )
        elif trust_root.licensing_policy is not None:
            expected_license = trust_root.licensing_policy.license_id
        else:  # Defensive: TrustRootDefinition already rejects this state.
            raise ValueError("trust root has no licensing policy")
        license_ok = (
            parsed.manifest.license == expected_license
            and ig.license == expected_license
        )
        cls._gate(
            checks,
            blockers,
            code=StructuredCheckCode.LICENSE_POLICY,
            passed=license_ok,
            blocker="LICENSE_DECLARATION_CONFLICT",
            pass_details="Package license declarations match trust-root policy.",
            block_details="Package license declarations conflict with trust-root policy.",
            diagnostics={
                "expected": expected_license,
                "manifest": parsed.manifest.license,
                "implementation_guide": ig.license,
            },
        )
        narratives = tuple(
            cls._narrative_link(item, parsed.declared_strings)
            for item in configured_narratives
        )
        narrative_ok = (
            not policy.require_declared_narrative_link
            or all(
                item.status is NarrativeLinkStatus.DECLARED_IN_PACKAGE
                for item in narratives
            )
        )
        cls._gate(
            checks,
            blockers,
            code=StructuredCheckCode.NARRATIVE_AUTHORITY,
            passed=narrative_ok,
            blocker="CONTROLLING_NARRATIVE_NOT_DECLARED",
            pass_details="Configured controlling narratives are declared by the package.",
            block_details="A configured controlling narrative is not declared by the package.",
            diagnostics={
                "links": [item.model_dump(mode="json") for item in narratives]
            },
        )
        narrative_input_check = cls._input_check(
            input_closure, StructuredInputCheckCode.NARRATIVE_ASSETS
        )
        narrative_assets_ok = (
            narrative_input_check is not None
            and narrative_input_check.outcome is StructuredInputCheckOutcome.PASS
        )
        cls._gate(
            checks,
            blockers,
            code=StructuredCheckCode.NARRATIVE_ARTIFACT_COVERAGE,
            passed=narrative_assets_ok,
            blocker="CONTROLLING_NARRATIVE_ASSETS_UNRESOLVED",
            pass_details="Controlling narrative assets are preserved in the signed input closure.",
            block_details="Controlling narrative assets lack a complete signed input closure.",
            diagnostics={
                "input_run_id": (
                    input_closure.content.input_run_id
                    if input_closure is not None
                    else None
                ),
                "artifact_count": (
                    len(input_closure.content.narrative_artifacts)
                    if input_closure is not None
                    else 0
                ),
            },
        )
        dependency_count = len(parsed.manifest.dependencies)
        dependency_input_check = cls._input_check(
            input_closure, StructuredInputCheckCode.DEPENDENCY_CLOSURE
        )
        closure_direct_dependencies = (
            input_closure.content.direct_dependencies
            if input_closure is not None
            else ()
        )
        dependency_closure_ok = (
            dependency_input_check is not None
            and dependency_input_check.outcome is StructuredInputCheckOutcome.PASS
            and closure_direct_dependencies == parsed.manifest.dependencies
        )
        dependencies_ok = (
            not policy.require_resolved_dependencies or dependency_closure_ok
        )
        cls._gate(
            checks,
            blockers,
            code=StructuredCheckCode.DEPENDENCY_COVERAGE,
            passed=dependencies_ok,
            blocker=f"FHIR_DEPENDENCIES_UNRESOLVED:{dependency_count}",
            pass_details="The package has no unresolved dependency closure.",
            block_details="FHIR dependencies have not been independently reconciled.",
            diagnostics={
                "dependencies": [
                    item.model_dump(mode="json")
                    for item in parsed.manifest.dependencies
                ],
                "input_run_id": (
                    input_closure.content.input_run_id
                    if input_closure is not None
                    else None
                ),
                "resolved_package_count": (
                    len(input_closure.content.dependency_packages)
                    if input_closure is not None
                    else 0
                ),
            },
        )
        return (
            tuple(checks),
            narratives,
            tuple(sorted(blockers)),
            tuple(sorted(warnings)),
        )

    @staticmethod
    def _input_check(
        report: StructuredInputClosureReport | None,
        code: StructuredInputCheckCode,
    ):
        if report is None:
            return None
        return next((item for item in report.content.checks if item.code is code), None)

    @staticmethod
    def _narrative_link(
        item: dict[str, str | None], declared_strings: frozenset[str]
    ) -> NarrativeAuthorityLink:
        declared = item["url"] in declared_strings or (
            item["identifier"] is not None
            and item["identifier"] in declared_strings
        )
        return NarrativeAuthorityLink(
            link_id=str(item["link_id"]),
            title=str(item["title"]),
            url=str(item["url"]),
            identifier=item["identifier"],
            version=item["version"],
            status=(
                NarrativeLinkStatus.DECLARED_IN_PACKAGE
                if declared
                else NarrativeLinkStatus.CONFIGURED_NOT_DECLARED
            ),
        )

    @staticmethod
    def _check(
        code: StructuredCheckCode,
        outcome: StructuredCheckOutcome,
        details: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> StructuredValidationCheck:
        return StructuredValidationCheck(
            code=code,
            outcome=outcome,
            details=details,
            diagnostics=diagnostics or {},
        )

    @classmethod
    def _gate(
        cls,
        checks: list[StructuredValidationCheck],
        blockers: list[str],
        *,
        code: StructuredCheckCode,
        passed: bool,
        blocker: str,
        pass_details: str,
        block_details: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        checks.append(
            cls._check(
                code,
                (
                    StructuredCheckOutcome.PASS
                    if passed
                    else StructuredCheckOutcome.BLOCK
                ),
                pass_details if passed else block_details,
                diagnostics,
            )
        )
        if not passed:
            blockers.append(blocker)
