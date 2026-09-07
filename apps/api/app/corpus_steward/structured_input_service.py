"""Acquire and attest immutable narrative and FHIR dependency closures."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from app.corpus_steward.connectors.base import (
    ConnectorError,
    ConnectorRequest,
    HTTPConnectorTransport,
)
from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.fhir_package import FHIRPackageParser, FHIRPackageValidationError
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.schemas import ArtifactKind, AttestationPurpose, TrustRootDefinition
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import (
    SQLStructuredInputRepository,
    StoredStructuredInputRun,
    StructuredInputRepositoryConflictError,
)
from app.corpus_steward.structured_input_schemas import (
    STRUCTURED_INPUT_RESOLVER_NAME,
    STRUCTURED_INPUT_RESOLVER_VERSION,
    DependencyRegistryPolicy,
    DependencyRequirement,
    NarrativeAssetDefinition,
    NarrativeAssetRole,
    NarrativeAuthorityDefinition,
    ResolvedDependencyPackage,
    ResolvedNarrativeArtifact,
    StructuredInputAttestationContent,
    StructuredInputCheck,
    StructuredInputCheckCode,
    StructuredInputCheckOutcome,
    StructuredInputClosureContent,
    StructuredInputClosureReport,
    StructuredInputIssue,
    StructuredInputResolutionResult,
    StructuredInputState,
)
from app.corpus_steward.structured_repository import (
    SQLStructuredPackageRepository,
    StructuredSourceContext,
)
from app.corpus_steward.structured_schemas import FHIRPackageDependency
from app.schemas.corpus import canonical_json_bytes, canonical_sha256
from app.schemas.domain import utc_now

# Acquisition topologies. A DAK candidate's source artifact is a FHIR package whose
# controlling narratives are separate assets; a narrative-anchored candidate's source
# artifact is the clinical narrative itself. See docs/narrative-only-materialization.md.
DAK_STRUCTURED = "DAK_STRUCTURED"
NARRATIVE_ANCHORED = "NARRATIVE_ANCHORED"

STRUCTURED_INPUT_ATTESTATION_PREDICATE = (
    "https://med-rag.local/attestations/structured/AUTHORITATIVE_INPUT_CLOSURE"
)


def source_topology(trust_root: TrustRootDefinition) -> str:
    """Which acquisition topology this publisher uses.

    Absent means today's DAK behaviour, so every existing signed trust root keeps
    resolving exactly as before. ``NARRATIVE_ANCHORED`` is the inverse topology: the
    inventory source artifact *is* the controlling clinical narrative, rather than a
    FHIR package with narratives hanging off it as side-channel assets.

    Module-level because more than one stage has to agree about the answer, and two
    stages parsing the same field independently is how they quietly stop agreeing.
    """

    declared = trust_root.connector_config.get("source_topology", DAK_STRUCTURED)
    if declared not in (DAK_STRUCTURED, NARRATIVE_ANCHORED):
        raise ValueError(f"unknown source_topology: {declared!r}")
    return str(declared)
_PACKAGE_ID = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
_EXACT_VERSION = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_PATCH_RANGE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.x$")


class StructuredInputAcquisitionError(RuntimeError):
    def __init__(self, reason_code: str, subject: str, details: str) -> None:
        self.reason_code = reason_code
        self.subject = subject
        super().__init__(details)


@dataclass(frozen=True)
class _PendingRequirement:
    parent_package: str
    dependency: FHIRPackageDependency
    depth: int
    direct: bool


@dataclass(frozen=True)
class _DependencyAcquisition:
    requested_version: str
    package: ResolvedDependencyPackage


class StructuredInputClosureService:
    def __init__(
        self,
        *,
        trust_roots: SQLTrustRootRegistry,
        source_repository: SQLStructuredPackageRepository,
        input_repository: SQLStructuredInputRepository,
        ledger: SQLReconciliationLedger,
        attestations: SQLAttestationRepository,
        artifacts: ImmutableStewardArtifactStore,
        parser: FHIRPackageParser,
        transport: HTTPConnectorTransport,
        signer: Ed25519Signer,
    ) -> None:
        self._trust_roots = trust_roots
        self._source_repository = source_repository
        self._input_repository = input_repository
        self._ledger = ledger
        self._attestations = attestations
        self._artifacts = artifacts
        self._parser = parser
        self._transport = transport
        self._signer = signer

    async def resolve(
        self, candidate_id: str, *, item_id: str | None = None
    ) -> StructuredInputResolutionResult:
        source = await self._source_repository.source_context(
            candidate_id, item_id=item_id
        )
        existing = await self._input_repository.existing(
            candidate_id=candidate_id,
            item_id=source.inventory_item.item_id,
            resolver_name=STRUCTURED_INPUT_RESOLVER_NAME,
            resolver_version=STRUCTURED_INPUT_RESOLVER_VERSION,
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
        # Read once, and branch before `_policy`: on the narrative path `_policy` returns
        # an empty narrative tuple and no dependency policy, so computing it first only to
        # discard it invites a reader to think the narrative path uses it.
        topology = self._topology(trust_root)
        source_content = self._artifacts.read(source.artifact_storage_key)
        if hashlib.sha256(source_content).hexdigest() != source.source_artifact.artifact_sha256:
            raise ValueError("preserved source artifact digest verification failed")

        if topology == NARRATIVE_ANCHORED:
            self._policy(trust_root)  # validates that no DAK-only config is declared
            return await self._resolve_narrative_anchored(
                source=source,
                candidate_id=candidate_id,
                trust_root=trust_root,
                source_content=source_content,
            )

        narratives, dependency_policy = self._policy(trust_root)
        issues: dict[tuple[str, str], StructuredInputIssue] = {}
        checks: list[StructuredInputCheck] = []
        direct_dependencies: tuple[FHIRPackageDependency, ...] = ()
        try:
            parsed = self._parser.parse(source_content)
            direct_dependencies = parsed.manifest.dependencies
            checks.append(
                self._check(
                    StructuredInputCheckCode.SOURCE_PACKAGE,
                    StructuredInputCheckOutcome.PASS,
                    "Source package dependency declarations were parsed safely.",
                    {"direct_dependency_count": len(direct_dependencies)},
                )
            )
        except FHIRPackageValidationError as error:
            self._issue(
                issues,
                subject=source.inventory_item.item_id,
                reason_code=error.reason_code,
                details=str(error),
            )
            checks.append(
                self._check(
                    StructuredInputCheckCode.SOURCE_PACKAGE,
                    StructuredInputCheckOutcome.BLOCK,
                    "Source package could not establish dependency inputs.",
                    {"reason_code": error.reason_code},
                )
            )

        narrative_artifacts = await self._acquire_narratives(
            trust_root, narratives, issues
        )
        expected_asset_ids = tuple(
            f"{narrative.link_id}:{asset.asset_id}"
            for narrative in narratives
            for asset in narrative.assets
        )
        narrative_complete = len(narrative_artifacts) == len(expected_asset_ids)
        checks.append(
            self._check(
                StructuredInputCheckCode.NARRATIVE_ASSETS,
                (
                    StructuredInputCheckOutcome.PASS
                    if narrative_complete
                    else StructuredInputCheckOutcome.BLOCK
                ),
                (
                    "Every configured controlling narrative asset was preserved."
                    if narrative_complete
                    else "One or more controlling narrative assets could not be preserved."
                ),
                {
                    "expected_count": len(expected_asset_ids),
                    "resolved_count": len(narrative_artifacts),
                },
            )
        )

        requirements: tuple[DependencyRequirement, ...] = ()
        dependency_packages: tuple[ResolvedDependencyPackage, ...] = ()
        dependency_complete = False
        if checks[0].outcome is StructuredInputCheckOutcome.PASS:
            requirements, dependency_packages, dependency_complete = (
                await self._resolve_dependencies(
                    root_item_id=source.inventory_item.item_id,
                    direct_dependencies=direct_dependencies,
                    policy=dependency_policy,
                    issues=issues,
                )
            )
        checks.append(
            self._check(
                StructuredInputCheckCode.DEPENDENCY_CLOSURE,
                (
                    StructuredInputCheckOutcome.PASS
                    if dependency_complete
                    else StructuredInputCheckOutcome.BLOCK
                ),
                (
                    "The complete pinned FHIR dependency graph was preserved."
                    if dependency_complete
                    else "The FHIR dependency graph is incomplete or ambiguous."
                ),
                {
                    "direct_dependency_count": len(direct_dependencies),
                    "requirement_count": len(requirements),
                    "resolved_package_count": len(dependency_packages),
                },
            )
        )

        blockers = self._blockers(checks, issues.values())
        completed_at = utc_now()
        run_id = self._run_id(candidate_id, source.inventory_item.item_id)
        dependency_packages = tuple(
            sorted(
                dependency_packages,
                key=lambda item: (item.package_id, item.version),
            )
        )
        narrative_artifacts = tuple(
            sorted(
                narrative_artifacts,
                key=lambda item: (item.link_id, item.asset_id),
            )
        )
        dependency_payload = [
            item.model_dump(mode="json") for item in dependency_packages
        ]
        narrative_payload = [
            item.model_dump(mode="json") for item in narrative_artifacts
        ]
        content = StructuredInputClosureContent(
            input_run_id=run_id,
            reconciliation_candidate_id=candidate_id,
            trust_root_id=trust_root.trust_root_id,
            trust_root_sha256=trust_root.sha256,
            inventory_item_id=source.inventory_item.item_id,
            source_artifact_sha256=source.source_artifact.artifact_sha256,
            completed_at=completed_at,
            direct_dependencies=direct_dependencies,
            requirements=requirements,
            dependency_packages=dependency_packages,
            dependency_inventory_sha256=(
                canonical_sha256({"dependencies": dependency_payload})
                if dependency_payload
                else None
            ),
            expected_narrative_asset_ids=expected_asset_ids,
            narrative_artifacts=narrative_artifacts,
            narrative_inventory_sha256=(
                canonical_sha256({"narratives": narrative_payload})
                if narrative_payload
                else None
            ),
            issues=tuple(issues.values()),
            checks=tuple(checks),
            complete=not blockers and not issues,
            blockers=blockers,
        )
        return await self._seal_and_record(
            content,
            source=source,
            candidate_id=candidate_id,
            trust_root=trust_root,
            run_id=run_id,
            completed_at=completed_at,
        )

    async def _result(
        self, existing: StoredStructuredInputRun
    ) -> StructuredInputResolutionResult:
        return StructuredInputResolutionResult(
            state=existing.state,
            report=existing.report,
            attestation=await self._attestations.get_reference(existing.attestation_id),
            report_artifact_sha256=existing.report_artifact_sha256,
        )


    async def _resolve_narrative_anchored(
        self,
        *,
        source: StructuredSourceContext,
        candidate_id: str,
        trust_root: TrustRootDefinition,
        source_content: bytes,
    ) -> StructuredInputResolutionResult:
        """Resolve a closure whose source artifact *is* the controlling narrative.

        No FHIR parse, no dependency graph, and above all **no refetch**: the narrative
        artifact is synthesized from the bytes reconciliation already preserved and whose
        digest was verified by the caller. Fetching the same document a second time under
        a narrative asset kind would give one set of bytes two provenance stories, which
        `SQLReconciliationLedger.record_artifacts` refuses by design.

        The two checks the DAK path uses to gate promotion still appear, so a reader
        comparing two closure reports sees the same vocabulary: `SOURCE_PACKAGE` passes on
        the narrative document's own identity, and `DEPENDENCY_CLOSURE` passes vacuously
        because a narrative document has no dependency graph to be incomplete.
        """

        issues: dict[tuple[str, str], StructuredInputIssue] = {}
        checks: list[StructuredInputCheck] = []
        item = source.inventory_item
        reference = source.source_artifact
        checks.append(
            self._check(
                StructuredInputCheckCode.SOURCE_PACKAGE,
                StructuredInputCheckOutcome.PASS,
                "Source artifact is the controlling clinical narrative; no package parse applies.",
                {"topology": NARRATIVE_ANCHORED, "byte_size": len(source_content)},
            )
        )

        # The two checks the DAK path runs before it will treat bytes as a controlling
        # narrative. Skipping them here would let a trust root with no licensing entry, or
        # a truncated or mistyped source artifact, still produce a *signed* RESOLVED
        # closure asserting the bytes are the clinical authority.
        narrative_failures: list[str] = []
        try:
            license_policy = trust_root.license_for_asset(item.item_id)
        except ValueError as error:
            license_policy = None
            narrative_failures.append("NARRATIVE_LICENSE_POLICY_MISSING")
            self._issue(
                issues,
                subject=item.item_id,
                reason_code="NARRATIVE_LICENSE_POLICY_MISSING",
                details=str(error),
            )
        if license_policy is not None and not license_policy.acquisition_allowed:
            narrative_failures.append("NARRATIVE_ACQUISITION_FORBIDDEN")
            self._issue(
                issues,
                subject=item.item_id,
                reason_code="NARRATIVE_ACQUISITION_FORBIDDEN",
                details="trust root forbids acquisition of this narrative asset",
            )
        try:
            self._validate_narrative_content(reference.media_type, source_content)
        except (ValueError, zipfile.BadZipFile) as error:
            narrative_failures.append("NARRATIVE_CONTENT_INVALID")
            self._issue(
                issues,
                subject=item.item_id,
                reason_code="NARRATIVE_CONTENT_INVALID",
                details=str(error),
            )

        narrative = ResolvedNarrativeArtifact(
            # asset_id == item_id is the topology, not a shortcut: the licensing policy
            # and scope_item_ids in a narrative-anchored trust root are keyed by the same
            # publisher record identifier.
            link_id=item.item_id,
            asset_id=item.item_id,
            title=item.title or item.item_id,
            role=NarrativeAssetRole.PRIMARY,
            configured_url=reference.requested_url,
            final_url=reference.final_url,
            media_type=reference.media_type,
            artifact_sha256=reference.artifact_sha256,
            byte_size=reference.byte_size,
            fetched_at=reference.fetched_at,
            etag=reference.etag,
            last_modified=reference.last_modified,
        )
        narrative_ok = not narrative_failures
        checks.append(
            self._check(
                StructuredInputCheckCode.NARRATIVE_ASSETS,
                (
                    StructuredInputCheckOutcome.PASS
                    if narrative_ok
                    else StructuredInputCheckOutcome.BLOCK
                ),
                (
                    "The controlling narrative is the preserved source artifact."
                    if narrative_ok
                    else "The preserved source artifact is not usable as a controlling narrative."
                ),
                {
                    "expected_count": 1,
                    "resolved_count": 1 if narrative_ok else 0,
                    "failures": sorted(set(narrative_failures)),
                },
            )
        )
        checks.append(
            self._check(
                StructuredInputCheckCode.DEPENDENCY_CLOSURE,
                StructuredInputCheckOutcome.PASS,
                "A narrative document declares no FHIR dependency graph.",
                {"direct_dependency_count": 0, "requirement_count": 0,
                 "resolved_package_count": 0},
            )
        )

        blockers = self._blockers(checks, issues.values())
        completed_at = utc_now()
        run_id = self._run_id(candidate_id, item.item_id)
        narrative_payload = [narrative.model_dump(mode="json")]
        content = StructuredInputClosureContent(
            input_run_id=run_id,
            reconciliation_candidate_id=candidate_id,
            trust_root_id=trust_root.trust_root_id,
            trust_root_sha256=trust_root.sha256,
            inventory_item_id=item.item_id,
            source_artifact_sha256=reference.artifact_sha256,
            completed_at=completed_at,
            expected_narrative_asset_ids=(narrative.asset_id,),
            narrative_artifacts=(narrative,),
            narrative_inventory_sha256=canonical_sha256({"narratives": narrative_payload}),
            issues=tuple(issues.values()),
            checks=tuple(checks),
            complete=not blockers and not issues,
            blockers=blockers,
        )
        return await self._seal_and_record(
            content,
            source=source,
            candidate_id=candidate_id,
            trust_root=trust_root,
            run_id=run_id,
            completed_at=completed_at,
        )

    async def _seal_and_record(
        self,
        content: StructuredInputClosureContent,
        *,
        source: StructuredSourceContext,
        candidate_id: str,
        trust_root: TrustRootDefinition,
        run_id: str,
        completed_at: datetime,
    ) -> StructuredInputResolutionResult:
        """Seal, attest, and persist a closure report.

        Shared by both topologies deliberately. Duplicating a signing and persistence path
        is how two paths quietly stop agreeing about what a signed closure means, and the
        narrative topology has to produce a closure indistinguishable in kind from the DAK
        one - same predicate, same artifact kind, same conflict handling.
        """

        report = StructuredInputClosureReport.seal(content)
        report_artifact = self._artifacts.put(
            canonical_json_bytes(report),
            media_type="application/vnd.med-rag.structured-input-closure+json",
            kind=ArtifactKind.INPUT_CLOSURE_REPORT,
        )
        await self._ledger.record_artifact(report_artifact)
        statement = StructuredInputAttestationContent(
            input_run_id=run_id,
            reconciliation_candidate_id=candidate_id,
            trust_root_id=trust_root.trust_root_id,
            trust_root_sha256=trust_root.sha256,
            inventory_item_id=source.inventory_item.item_id,
            source_artifact_sha256=source.source_artifact.artifact_sha256,
            report_sha256=report.report_sha256,
            completed_at=completed_at,
        )
        attestation = await self._attestations.record_and_verify(
            statement,
            self._signer.sign(canonical_json_bytes(statement)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=STRUCTURED_INPUT_ATTESTATION_PREDICATE,
        )
        state = (
            StructuredInputState.RESOLVED
            if content.complete
            else StructuredInputState.BLOCKED
        )
        try:
            await self._input_repository.record(
                state=state,
                report=report,
                report_artifact_sha256=report_artifact.sha256,
                attestation_id=attestation.attestation_id,
            )
        except StructuredInputRepositoryConflictError:
            concurrent = await self._input_repository.existing(
                candidate_id=candidate_id,
                item_id=source.inventory_item.item_id,
                resolver_name=STRUCTURED_INPUT_RESOLVER_NAME,
                resolver_version=STRUCTURED_INPUT_RESOLVER_VERSION,
            )
            if concurrent is None or concurrent.report.report_sha256 != report.report_sha256:
                raise
            return await self._result(concurrent)
        return StructuredInputResolutionResult(
            state=state,
            report=report,
            attestation=attestation,
            report_artifact_sha256=report_artifact.sha256,
        )

    async def _acquire_narratives(
        self,
        trust_root: TrustRootDefinition,
        narratives: tuple[NarrativeAuthorityDefinition, ...],
        issues: dict[tuple[str, str], StructuredInputIssue],
    ) -> tuple[ResolvedNarrativeArtifact, ...]:
        async def acquire(
            narrative: NarrativeAuthorityDefinition,
            asset: NarrativeAssetDefinition,
        ) -> ResolvedNarrativeArtifact | StructuredInputAcquisitionError:
            subject = f"{narrative.link_id}:{asset.asset_id}"
            try:
                if trust_root.asset_licensing:
                    license_policy = trust_root.license_for_asset(asset.asset_id)
                    if not license_policy.acquisition_allowed:
                        raise StructuredInputAcquisitionError(
                            "NARRATIVE_LICENSE_PROHIBITS_ACQUISITION",
                            subject,
                            "the asset-specific license policy prohibits acquisition",
                        )
                response = await self._transport.fetch(
                    ConnectorRequest(url=asset.url, accept=asset.media_type),
                    allowed_domains=trust_root.allowed_domains,
                )
                self._validate_narrative_content(asset.media_type, response.body)
                digest = hashlib.sha256(response.body).hexdigest()
                if asset.expected_sha256 is not None and digest != asset.expected_sha256:
                    raise StructuredInputAcquisitionError(
                        "NARRATIVE_DIGEST_MISMATCH",
                        subject,
                        "narrative artifact does not match its pinned digest",
                    )
                stored = self._artifacts.put(
                    response.body,
                    media_type=asset.media_type,
                    kind=ArtifactKind.NARRATIVE_SOURCE,
                )
                await self._ledger.record_artifact(stored)
                return ResolvedNarrativeArtifact(
                    link_id=narrative.link_id,
                    asset_id=asset.asset_id,
                    title=asset.title,
                    role=asset.role,
                    configured_url=asset.url,
                    final_url=response.final_url,
                    media_type=asset.media_type,
                    artifact_sha256=stored.sha256,
                    byte_size=stored.byte_size,
                    fetched_at=response.fetched_at,
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                )
            except StructuredInputAcquisitionError as error:
                return error
            except (ConnectorError, ValueError, zipfile.BadZipFile) as error:
                return StructuredInputAcquisitionError(
                    "NARRATIVE_ACQUISITION_FAILED", subject, str(error)
                )

        results = await asyncio.gather(
            *(
                acquire(narrative, asset)
                for narrative in narratives
                for asset in narrative.assets
            )
        )
        artifacts: list[ResolvedNarrativeArtifact] = []
        for result in results:
            if isinstance(result, StructuredInputAcquisitionError):
                self._issue(
                    issues,
                    subject=result.subject,
                    reason_code=result.reason_code,
                    details=str(result),
                )
            else:
                artifacts.append(result)
        return tuple(artifacts)

    async def _resolve_dependencies(
        self,
        *,
        root_item_id: str,
        direct_dependencies: tuple[FHIRPackageDependency, ...],
        policy: DependencyRegistryPolicy,
        issues: dict[tuple[str, str], StructuredInputIssue],
    ) -> tuple[
        tuple[DependencyRequirement, ...],
        tuple[ResolvedDependencyPackage, ...],
        bool,
    ]:
        pending = [
            _PendingRequirement(
                parent_package=f"ROOT:{root_item_id}",
                dependency=dependency,
                depth=1,
                direct=True,
            )
            for dependency in direct_dependencies
        ]
        seen_requirements: set[tuple[str, str, str]] = set()
        acquisitions: dict[tuple[str, str], _DependencyAcquisition] = {}
        packages: dict[tuple[str, str], ResolvedDependencyPackage] = {}
        requirements: list[DependencyRequirement] = []
        total_bytes = 0
        semaphore = asyncio.Semaphore(policy.concurrency)

        while pending:
            wave: list[_PendingRequirement] = []
            for item in pending:
                key = (
                    item.parent_package,
                    item.dependency.package_id,
                    item.dependency.version,
                )
                if key not in seen_requirements:
                    seen_requirements.add(key)
                    wave.append(item)
            pending = []
            if not wave:
                break
            fetch_keys = {
                (item.dependency.package_id, item.dependency.version)
                for item in wave
                if (item.dependency.package_id, item.dependency.version)
                not in acquisitions
            }
            if len(acquisitions) + len(fetch_keys) > policy.max_packages:
                self._issue(
                    issues,
                    subject="dependency-closure",
                    reason_code="DEPENDENCY_PACKAGE_LIMIT",
                    details="dependency closure exceeds the configured package limit",
                )
                break

            async def fetch(
                package_id: str, version_spec: str
            ) -> _DependencyAcquisition | StructuredInputAcquisitionError:
                async with semaphore:
                    try:
                        return await self._acquire_dependency(
                            package_id, version_spec, policy
                        )
                    except StructuredInputAcquisitionError as error:
                        return error

            fetched = await asyncio.gather(
                *(fetch(package_id, version) for package_id, version in fetch_keys)
            )
            for result in fetched:
                if isinstance(result, StructuredInputAcquisitionError):
                    self._issue(
                        issues,
                        subject=result.subject,
                        reason_code=result.reason_code,
                        details=str(result),
                    )
                else:
                    acquisitions[(result.package.package_id, result.requested_version)] = (
                        result
                    )

            newly_added: list[tuple[ResolvedDependencyPackage, int]] = []
            for item in wave:
                lookup = (item.dependency.package_id, item.dependency.version)
                acquired = acquisitions.get(lookup)
                if acquired is None:
                    continue
                package = acquired.package
                package_key = (package.package_id, package.version)
                requirements.append(
                    DependencyRequirement(
                        parent_package=item.parent_package,
                        package_id=package.package_id,
                        version_spec=item.dependency.version,
                        resolved_version=package.version,
                        depth=item.depth,
                        direct=item.direct,
                    )
                )
                existing = packages.get(package_key)
                if existing is not None:
                    if existing.package_artifact_sha256 != package.package_artifact_sha256:
                        self._issue(
                            issues,
                            subject=f"{package.package_id}#{package.version}",
                            reason_code="DEPENDENCY_CONTENT_CONFLICT",
                            details="one package version resolved to multiple artifacts",
                        )
                    continue
                packages[package_key] = package
                total_bytes += package.byte_size
                newly_added.append((package, item.depth))
            if total_bytes > policy.max_total_bytes:
                self._issue(
                    issues,
                    subject="dependency-closure",
                    reason_code="DEPENDENCY_TOTAL_SIZE_LIMIT",
                    details="dependency closure exceeds the configured total byte limit",
                )
                break
            for package, parent_depth in newly_added:
                child_depth = parent_depth + 1
                if package.manifest.dependencies and child_depth > policy.max_depth:
                    self._issue(
                        issues,
                        subject=f"{package.package_id}#{package.version}",
                        reason_code="DEPENDENCY_DEPTH_LIMIT",
                        details="dependency closure exceeds the configured depth limit",
                    )
                    continue
                parent = f"{package.package_id}#{package.version}"
                pending.extend(
                    _PendingRequirement(
                        parent_package=parent,
                        dependency=dependency,
                        depth=child_depth,
                        direct=False,
                    )
                    for dependency in package.manifest.dependencies
                )

        if self._has_cycle(requirements):
            self._issue(
                issues,
                subject="dependency-closure",
                reason_code="DEPENDENCY_CYCLE",
                details="dependency closure contains a package cycle",
            )
        dependency_issues = any(
            issue.reason_code.startswith("DEPENDENCY_") for issue in issues.values()
        )
        complete = not dependency_issues and len(requirements) == len(seen_requirements)
        return tuple(requirements), tuple(packages.values()), complete

    async def _acquire_dependency(
        self,
        package_id: str,
        version_spec: str,
        policy: DependencyRegistryPolicy,
    ) -> _DependencyAcquisition:
        subject = f"{package_id}#{version_spec}"
        if not _PACKAGE_ID.fullmatch(package_id):
            raise StructuredInputAcquisitionError(
                "DEPENDENCY_ID_INVALID", subject, "FHIR dependency package ID is invalid"
            )
        registry_url = f"{policy.base_url.rstrip('/')}/{package_id}"
        try:
            metadata_response = await self._transport.fetch(
                ConnectorRequest(url=registry_url, accept="application/json"),
                allowed_domains=policy.allowed_domains,
            )
            metadata = self._registry_metadata(metadata_response.body, package_id)
            resolved_version = self._resolve_version(version_spec, metadata["versions"])
            version_record = metadata["versions"][resolved_version]
            dist = version_record.get("dist")
            if not isinstance(dist, dict) or not isinstance(dist.get("tarball"), str):
                raise StructuredInputAcquisitionError(
                    "DEPENDENCY_REGISTRY_INVALID",
                    subject,
                    "registry version metadata has no tarball URL",
                )
            tarball_url = policy.package_url(
                package_id, resolved_version, dist["tarball"]
            )
            shasum = dist.get("shasum")
            if shasum is not None and (
                not isinstance(shasum, str)
                or re.fullmatch(r"[0-9a-f]{40}", shasum) is None
            ):
                raise StructuredInputAcquisitionError(
                    "DEPENDENCY_REGISTRY_INVALID",
                    subject,
                    "registry tarball SHA-1 declaration is invalid",
                )
            metadata_artifact = self._artifacts.put(
                metadata_response.body,
                media_type="application/json",
                kind=ArtifactKind.DEPENDENCY_METADATA,
            )
            await self._ledger.record_artifact(metadata_artifact)
            package_response = await self._transport.fetch(
                ConnectorRequest(url=tarball_url, accept="application/gzip"),
                allowed_domains=policy.allowed_domains,
            )
            if shasum is not None and hashlib.sha1(
                package_response.body, usedforsecurity=False
            ).hexdigest() != shasum:
                raise StructuredInputAcquisitionError(
                    "DEPENDENCY_REGISTRY_DIGEST_MISMATCH",
                    subject,
                    "dependency package does not match registry shasum",
                )
            manifest = self._parser.parse_dependency_manifest(package_response.body)
            if manifest.package_id != package_id or manifest.version != resolved_version:
                raise StructuredInputAcquisitionError(
                    "DEPENDENCY_IDENTITY_MISMATCH",
                    subject,
                    "dependency tarball identity differs from registry metadata",
                )
            package_artifact = self._artifacts.put(
                package_response.body,
                media_type="application/gzip",
                kind=ArtifactKind.DEPENDENCY_PACKAGE,
            )
            await self._ledger.record_artifact(package_artifact)
            package = ResolvedDependencyPackage(
                package_id=package_id,
                version=resolved_version,
                registry_metadata_artifact_sha256=metadata_artifact.sha256,
                package_artifact_sha256=package_artifact.sha256,
                byte_size=package_artifact.byte_size,
                registry_url=registry_url,
                tarball_url=tarball_url,
                registry_sha1=shasum,
                fetched_at=package_response.fetched_at,
                etag=package_response.headers.get("etag"),
                last_modified=package_response.headers.get("last-modified"),
                manifest=manifest,
            )
            return _DependencyAcquisition(
                requested_version=version_spec, package=package
            )
        except StructuredInputAcquisitionError:
            raise
        except (ConnectorError, FHIRPackageValidationError, ValueError) as error:
            raise StructuredInputAcquisitionError(
                "DEPENDENCY_ACQUISITION_FAILED", subject, str(error)
            ) from error

    @staticmethod
    def _registry_metadata(content: bytes, package_id: str) -> dict[str, Any]:
        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate registry JSON key: {key}")
                result[key] = value
            return result

        try:
            payload = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=reject_duplicates,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite registry JSON number: {value}")
                ),
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            ValueError,
        ) as error:
            raise ValueError("dependency registry metadata is not strict JSON") from error
        if (
            not isinstance(payload, dict)
            or payload.get("name") != package_id
            or not isinstance(payload.get("versions"), dict)
        ):
            raise ValueError("dependency registry metadata identity is invalid")
        if StructuredInputClosureService._json_depth(payload) > 50:
            raise ValueError("dependency registry metadata exceeds the JSON depth limit")
        return payload

    @staticmethod
    def _resolve_version(version_spec: str, versions: dict[str, Any]) -> str:
        if _EXACT_VERSION.fullmatch(version_spec):
            if version_spec not in versions:
                raise ValueError(f"registry does not contain exact version {version_spec}")
            return version_spec
        patch = _PATCH_RANGE.fullmatch(version_spec)
        if patch is None:
            raise ValueError(f"dependency version is not deterministic: {version_spec}")
        major, minor = (int(patch.group(1)), int(patch.group(2)))
        matching: list[tuple[int, str]] = []
        for version in versions:
            match = _EXACT_VERSION.fullmatch(version)
            if match is None or "-" in version or "+" in version:
                continue
            parts = tuple(int(item) for item in version.split("."))
            if parts[:2] == (major, minor):
                matching.append((parts[2], version))
        if not matching:
            raise ValueError(f"registry cannot resolve patch range {version_spec}")
        return max(matching)[1]

    @staticmethod
    def _validate_narrative_content(media_type: str, content: bytes) -> None:
        """Structural validation of narrative bytes, by media type.

        Takes the media type rather than an asset definition so both topologies can call
        it: a narrative-anchored candidate has no `NarrativeAssetDefinition`, because its
        narrative is the inventory source artifact itself.
        """

        if media_type == "application/pdf":
            if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
                raise ValueError("narrative PDF signature or terminator is invalid")
            return
        if media_type == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ):
            if not content.startswith(b"PK\x03\x04"):
                raise ValueError("narrative XLSX does not have a ZIP signature")
            with zipfile.ZipFile(io.BytesIO(content)) as workbook:
                entries = workbook.infolist()
                if len(entries) > 10_000:
                    raise ValueError("narrative XLSX exceeds the entry limit")
                total_size = 0
                names: set[str] = set()
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                        raise ValueError("narrative XLSX contains an unsafe member path")
                    if entry.flag_bits & 0x1:
                        raise ValueError("encrypted narrative XLSX members are forbidden")
                    if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("narrative XLSX links are forbidden")
                    if entry.filename in names:
                        raise ValueError("narrative XLSX contains duplicate member paths")
                    total_size += entry.file_size
                    if total_size > 256 * 1024 * 1024:
                        raise ValueError("narrative XLSX exceeds expansion limits")
                    names.add(entry.filename)
                if content and total_size / len(content) > 100:
                    raise ValueError("narrative XLSX exceeds the compression-ratio limit")
                if not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
                    raise ValueError("narrative XLSX is missing required workbook members")
            return
        raise ValueError(f"unsupported narrative media type: {media_type}")

    @classmethod
    def _json_depth(cls, value: Any) -> int:
        if isinstance(value, dict):
            return 1 + max((cls._json_depth(item) for item in value.values()), default=0)
        if isinstance(value, list):
            return 1 + max((cls._json_depth(item) for item in value), default=0)
        return 0

    @staticmethod
    def _has_cycle(requirements: list[DependencyRequirement]) -> bool:
        graph: dict[str, set[str]] = {}
        for requirement in requirements:
            if requirement.parent_package.startswith("ROOT:"):
                continue
            child = f"{requirement.package_id}#{requirement.resolved_version}"
            graph.setdefault(requirement.parent_package, set()).add(child)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            if any(visit(child) for child in graph.get(node, ())):
                return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in tuple(graph))

    @staticmethod
    def _topology(trust_root: TrustRootDefinition) -> str:
        return source_topology(trust_root)

    @staticmethod
    def _policy(
        trust_root: TrustRootDefinition,
    ) -> tuple[
        tuple[NarrativeAuthorityDefinition, ...], DependencyRegistryPolicy | None
    ]:
        # A narrative-anchored publisher declares neither block: there is no FHIR package
        # to have dependencies, and the narrative is the source artifact itself, resolved
        # per item through its own handle rather than statically bound here.
        if StructuredInputClosureService._topology(trust_root) == NARRATIVE_ANCHORED:
            for forbidden in ("controlling_narratives", "dependency_registry"):
                if trust_root.connector_config.get(forbidden):
                    raise ValueError(
                        f"a NARRATIVE_ANCHORED trust root must not declare {forbidden}"
                    )
            return ((), None)
        raw_narratives = trust_root.connector_config.get("controlling_narratives")
        if not isinstance(raw_narratives, list) or not raw_narratives:
            raise ValueError("trust root requires controlling narrative definitions")
        narratives = tuple(
            NarrativeAuthorityDefinition.model_validate(item)
            for item in raw_narratives
        )
        ids = [item.link_id for item in narratives]
        if len(ids) != len(set(ids)):
            raise ValueError("controlling narrative link IDs must be unique")
        raw_registry = trust_root.connector_config.get("dependency_registry")
        if not isinstance(raw_registry, dict):
            raise ValueError("trust root requires dependency_registry policy")
        return (
            tuple(sorted(narratives, key=lambda item: item.link_id)),
            DependencyRegistryPolicy.model_validate(raw_registry),
        )

    @staticmethod
    def _run_id(candidate_id: str, item_id: str) -> str:
        identity = ":".join(
            (
                candidate_id,
                item_id,
                STRUCTURED_INPUT_RESOLVER_NAME,
                STRUCTURED_INPUT_RESOLVER_VERSION,
            )
        )
        return "SIR_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _check(
        code: StructuredInputCheckCode,
        outcome: StructuredInputCheckOutcome,
        details: str,
        diagnostics: dict[str, object],
    ) -> StructuredInputCheck:
        return StructuredInputCheck(
            code=code, outcome=outcome, details=details, diagnostics=diagnostics
        )

    @staticmethod
    def _issue(
        issues: dict[tuple[str, str], StructuredInputIssue],
        *,
        subject: str,
        reason_code: str,
        details: str,
    ) -> None:
        issues.setdefault(
            (subject, reason_code),
            StructuredInputIssue(
                subject=subject, reason_code=reason_code, details=details
            ),
        )

    @staticmethod
    def _blockers(
        checks: list[StructuredInputCheck], issues
    ) -> tuple[str, ...]:
        blockers = {
            f"{issue.reason_code}:{issue.subject}"
            for issue in issues
        }
        blockers.update(
            f"{check.code.value}_INCOMPLETE"
            for check in checks
            if check.outcome is StructuredInputCheckOutcome.BLOCK
        )
        return tuple(sorted(blockers))
