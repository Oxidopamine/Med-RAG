import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx
import pymupdf
import pytest
from sqlalchemy import func, select

from app.corpus.releases import SQLCorpusReleaseRepository
from app.corpus_steward.connectors import HTTPConnectorTransport
from app.corpus_steward.crypto import Ed25519Signer, generate_ed25519_key_pair
from app.corpus_steward.evidence_extractor import DAKSourceExtractor
from app.corpus_steward.fhir_package import (
    FHIRPackageLimits,
    FHIRPackageParser,
    FHIRPackageValidationError,
)
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.materialization_repository import SQLMaterializationRepository
from app.corpus_steward.materialization_schemas import (
    AuthorityRole,
    MaterializationState,
)
from app.corpus_steward.materialization_service import MaterializationService
from app.corpus_steward.qa_repository import SQLQARepository
from app.corpus_steward.qa_schemas import QARunState
from app.corpus_steward.qa_service import QAService
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.schemas import AttestationPurpose, TrustRootDefinition
from app.corpus_steward.service import ReconciliationService
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import SQLStructuredInputRepository
from app.corpus_steward.structured_input_schemas import StructuredInputState
from app.corpus_steward.structured_input_service import StructuredInputClosureService
from app.corpus_steward.structured_repository import SQLStructuredPackageRepository
from app.corpus_steward.structured_schemas import StructuredRunState
from app.corpus_steward.structured_service import StructuredPackageService
from app.persistence.database import Database
from app.persistence.models import (
    CanonicalEvidenceRow,
    CorpusQARunRow,
    CorpusReleaseRow,
    CryptographicAttestationRow,
    EvidenceAnchorReplayRow,
    EvidenceQADecisionRow,
    MaterializationRunRow,
    MaterializedEvidenceRow,
    StructuredDependencyPackageRow,
    StructuredFHIRResourceRow,
    StructuredInputRunRow,
    StructuredNarrativeArtifactRow,
    StructuredNarrativeLinkRow,
    StructuredPackageRunRow,
)
from app.schemas.corpus import ReleaseState

NARRATIVE_URL = "https://guidance.example.test/controlling-narrative"


def _archive(members: list[tuple[str, bytes, str]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path, content, member_type in members:
            info = tarfile.TarInfo(path)
            info.size = len(content)
            if member_type == "file":
                archive.addfile(info, io.BytesIO(content))
            elif member_type == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "package/package.json"
                info.size = 0
                archive.addfile(info)
            else:
                raise AssertionError(f"unsupported test member type: {member_type}")
    return output.getvalue()


def fhir_package(
    *,
    experimental: bool = False,
    license_id: str = "CC-BY-4.0",
    include_narrative_authority: bool = True,
    dependencies: dict[str, str] | None = None,
    declared_reference: str = "PlanDefinition/demo-plan",
) -> bytes:
    manifest = {
        "name": "test.fhir",
        "version": "1.0.0",
        "canonical": "https://fhir.example.test/ig",
        "title": "Test FHIR implementation guide",
        "type": "IG",
        "fhirVersions": ["4.0.1"],
        "license": license_id,
        "dependencies": dependencies or {},
    }
    implementation_guide = {
        "resourceType": "ImplementationGuide",
        "id": "test.fhir",
        "url": f"{manifest['canonical']}/ImplementationGuide/test.fhir",
        "version": manifest["version"],
        "name": "TestFHIR",
        "title": manifest["title"],
        "status": "active",
        "experimental": experimental,
        "publisher": "Test Publisher",
        "packageId": manifest["name"],
        "license": license_id,
        "fhirVersion": ["4.0.1"],
        "meta": {"profile": []},
        "text": {"status": "generated", "div": "<div>Guide</div>"},
        "definition": {"resource": [{"reference": {"reference": declared_reference}}]},
    }
    if include_narrative_authority:
        implementation_guide["copyright"] = NARRATIVE_URL
    plan_definition = {
        "resourceType": "PlanDefinition",
        "id": "demo-plan",
        "url": "https://fhir.example.test/PlanDefinition/demo-plan",
        "version": "1.0.0",
        "status": "active",
        "experimental": False,
        "meta": {"profile": []},
        "text": {"status": "generated", "div": "<div>Plan</div>"},
    }
    return _archive(
        [
            (
                "package/package.json",
                json.dumps(manifest, separators=(",", ":")).encode(),
                "file",
            ),
            (
                "package/ImplementationGuide-test.fhir.json",
                json.dumps(implementation_guide, separators=(",", ":")).encode(),
                "file",
            ),
            (
                "package/PlanDefinition-demo-plan.json",
                json.dumps(plan_definition, separators=(",", ":")).encode(),
                "file",
            ),
        ]
    )


def dependency_package(
    package_id: str, version: str, dependencies: dict[str, str] | None = None
) -> bytes:
    return _archive(
        [
            (
                "package/package.json",
                json.dumps(
                    {
                        "name": package_id,
                        "version": version,
                        "title": package_id,
                        "type": "Core",
                        "fhirVersions": ["4.0.1"],
                        "license": "CC0-1.0",
                        "dependencies": dependencies or {},
                    },
                    separators=(",", ":"),
                ).encode(),
                "file",
            )
        ]
    )


def structured_definition(
    content: bytes, *, per_asset_licensing: bool = False
) -> TrustRootDefinition:
    payload = {
        "trust_root_id": "STRUCTURED_TEST",
        "publisher_id": "PUB_TEST",
        "publisher_name": "Test Publisher",
        "allowed_domains": ["fixtures.invalid"],
        "jurisdictions": ["WORLD"],
        "product_families": ["FHIR_IMPLEMENTATION_GUIDE"],
        "polling_interval_seconds": 3600,
        "connector_name": "synthetic",
        "connector_version": "1.0.0",
        "connector_config": {
            "structured_asset_id": "TEST_FHIR_COMPANION",
            "items": [
                {
                    "item_id": "test.fhir#1.0.0",
                    "title": "Test FHIR implementation guide",
                    "version": "1.0.0",
                    "lifecycle_status": "EFFECTIVE",
                    "media_type": "application/gzip",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            ],
            "controlling_narratives": [
                {
                    "assets": [
                        {
                            "asset_id": "TEST_NARRATIVE_MAIN",
                            "title": "Controlling narrative",
                            "role": "PRIMARY",
                            "url": "https://fixtures.invalid/narrative.pdf",
                            "media_type": "application/pdf",
                        }
                    ],
                    "link_id": "TEST_NARRATIVE",
                    "title": "Controlling narrative",
                    "url": NARRATIVE_URL,
                    "version": "1",
                }
            ],
            "dependency_registry": {
                "base_url": "https://fixtures.invalid/packages",
                "allowed_domains": ["fixtures.invalid"],
                "max_packages": 20,
                "max_depth": 5,
                "max_total_bytes": 10485760,
                "concurrency": 2,
            },
            "structured_policy": {
                "allowed_fhir_versions": ["4.0.1"],
                "allowed_publishers": ["Test Publisher"],
                "allow_experimental": False,
                "require_declared_narrative_link": True,
                "require_resolved_dependencies": True,
            },
        },
        "trusted_stage_key_ids": ["structured-test-key"],
    }
    if per_asset_licensing:
        payload["asset_licensing"] = [
            {
                "asset_id": "TEST_FHIR_COMPANION",
                "asset_kind": "INVENTORY_SOURCE",
                "license_id": "CC-BY-4.0",
                "policy_url": "https://fixtures.invalid/fhir-license",
                "acquisition_allowed": True,
                "evidence_materialization_allowed": False,
                "source_declared_license_id": FHIRPackageParser().parse(content).manifest.license,
            },
            {
                "asset_id": "TEST_NARRATIVE_MAIN",
                "asset_kind": "NARRATIVE_SOURCE",
                "license_id": "CC-BY-4.0",
                "policy_url": "https://fixtures.invalid/narrative-license",
                "acquisition_allowed": True,
                "evidence_materialization_allowed": True,
            },
        ]
    else:
        payload["licensing_policy"] = {
            "license_id": "CC-BY-4.0",
            "policy_url": "https://fixtures.invalid/license",
            "acquisition_allowed": True,
        }
    return TrustRootDefinition.model_validate(payload)


async def build_pipeline(
    tmp_path: Path,
    content: bytes,
    dependency_artifacts: dict[tuple[str, str], bytes] | None = None,
    registry_shasum_overrides: dict[tuple[str, str], str] | None = None,
    narrative_content: bytes = b"%PDF-1.7\nsynthetic narrative\n%%EOF",
    per_asset_licensing: bool = False,
):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'structured.sqlite3'}")
    await database.create_schema_for_tests()
    private_pem, _ = generate_ed25519_key_pair()
    private_path = tmp_path / "private.pem"
    private_path.write_bytes(private_pem)
    signer = Ed25519Signer.from_pem(
        private_path,
        key_id="structured-test-key",
        signer_identity="structured-test-steward",
    )
    attestations = SQLAttestationRepository(database)
    await attestations.register_key(
        key_id=signer.key_id,
        signer_identity=signer.signer_identity,
        public_key_pem=signer.public_key_pem(),
        purposes=(AttestationPurpose.STAGE,),
    )
    trust_roots = SQLTrustRootRegistry(database)
    await trust_roots.register(
        structured_definition(content, per_asset_licensing=per_asset_licensing)
    )
    ledger = SQLReconciliationLedger(database)
    artifacts = ImmutableStewardArtifactStore(tmp_path / "artifacts")
    registry_packages = dependency_artifacts or {
        ("hl7.fhir.r4.core", "4.0.1"): dependency_package("hl7.fhir.r4.core", "4.0.1")
    }
    shasum_overrides = registry_shasum_overrides or {}

    async def response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/narrative.pdf":
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=narrative_content,
                request=request,
            )
        if request.url.path.startswith("/packages/"):
            package_id = request.url.path.removeprefix("/packages/")
            versions = {
                version: {
                    "dist": {
                        "tarball": (
                            f"https://fixtures.invalid/tarballs/{candidate_id}-{version}.tgz"
                        ),
                        "shasum": shasum_overrides.get(
                            (candidate_id, version),
                            hashlib.sha1(artifact, usedforsecurity=False).hexdigest(),
                        ),
                    }
                }
                for (candidate_id, version), artifact in registry_packages.items()
                if candidate_id == package_id
            }
            if not versions:
                return httpx.Response(404, request=request)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "name": package_id,
                    "versions": versions,
                },
                request=request,
            )
        if request.url.path.startswith("/tarballs/"):
            filename = request.url.path.removeprefix("/tarballs/").removesuffix(".tgz")
            matches = [
                artifact
                for (package_id, version), artifact in registry_packages.items()
                if f"{package_id}-{version}" == filename
            ]
            if len(matches) != 1:
                return httpx.Response(404, request=request)
            return httpx.Response(
                200,
                headers={"content-type": "application/gzip"},
                content=matches[0],
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = HTTPConnectorTransport(
        max_bytes=10 * 1024 * 1024,
        timeout_seconds=5,
        allow_private_networks=True,
        transport=httpx.MockTransport(response),
    )
    reconciliation = ReconciliationService(
        trust_roots=trust_roots,
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        transport=transport,
        signer=signer,
    )
    input_repository = SQLStructuredInputRepository(database)
    inputs = StructuredInputClosureService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=input_repository,
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        parser=FHIRPackageParser(),
        transport=transport,
        signer=signer,
    )
    structured = StructuredPackageService(
        trust_roots=trust_roots,
        repository=SQLStructuredPackageRepository(database),
        input_repository=input_repository,
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        parser=FHIRPackageParser(),
        signer=signer,
    )
    return database, trust_roots, reconciliation, inputs, structured


def test_fhir_package_parser_indexes_complete_declared_inventory() -> None:
    parsed = FHIRPackageParser().parse(fhir_package())

    assert parsed.manifest.package_id == "test.fhir"
    assert parsed.implementation_guide.declared_resource_count == 1
    assert len(parsed.resources) == 2
    assert parsed.missing_declared_resources == ()
    assert parsed.undeclared_resources == ()
    assert NARRATIVE_URL in parsed.declared_strings


@pytest.mark.parametrize(
    ("content", "reason_code"),
    [
        (_archive([("../escape.json", b"{}", "file")]), "ARCHIVE_PATH_INVALID"),
        (
            _archive([("package/link.json", b"", "symlink")]),
            "ARCHIVE_MEMBER_TYPE",
        ),
        (
            _archive(
                [
                    (
                        "package/package.json",
                        b'{"name":"one","name":"two"}',
                        "file",
                    )
                ]
            ),
            "JSON_DUPLICATE_KEY",
        ),
    ],
)
def test_fhir_package_parser_rejects_unsafe_or_ambiguous_archives(
    content: bytes, reason_code: str
) -> None:
    with pytest.raises(FHIRPackageValidationError) as caught:
        FHIRPackageParser().parse(content)

    assert caught.value.reason_code == reason_code


def test_fhir_package_parser_enforces_member_limit() -> None:
    with pytest.raises(FHIRPackageValidationError) as caught:
        FHIRPackageParser(FHIRPackageLimits(max_member_bytes=16)).parse(fhir_package())

    assert caught.value.reason_code == "ARCHIVE_MEMBER_LIMIT"


def test_dependency_manifest_ignores_but_does_not_expand_nested_payloads() -> None:
    package = _archive(
        [
            (
                "package/package.json",
                json.dumps(
                    {
                        "name": "example.dependency",
                        "version": "1.0.0",
                        "dependencies": {},
                    }
                ).encode(),
                "file",
            ),
            ("package/other/template.zip", b"opaque nested bytes", "file"),
            ("openapi/Money.schema.json", b"{}" * 1024, "file"),
        ]
    )

    manifest = FHIRPackageParser(FHIRPackageLimits(max_member_bytes=512)).parse_dependency_manifest(
        package
    )

    assert manifest.package_id == "example.dependency"
    with pytest.raises(FHIRPackageValidationError) as caught:
        FHIRPackageParser().parse(package)
    assert caught.value.reason_code == "NESTED_ARCHIVE"


async def test_structured_pipeline_is_attested_idempotent_and_revision_bound(
    tmp_path,
) -> None:
    database, trust_roots, reconciliation, inputs, structured = await build_pipeline(
        tmp_path, fhir_package()
    )
    reconciled = await reconciliation.reconcile(
        "STRUCTURED_TEST", idempotency_key="structured-valid"
    )
    assert reconciled.release_candidate is not None
    resolved = await inputs.resolve(reconciled.release_candidate.content.candidate_id)
    repeated_inputs = await inputs.resolve(reconciled.release_candidate.content.candidate_id)
    assert resolved.state is StructuredInputState.RESOLVED
    assert repeated_inputs == resolved
    assert len(resolved.report.content.narrative_artifacts) == 1

    changed = structured_definition(fhir_package()).model_dump(mode="json")
    changed["licensing_policy"]["license_id"] = "CHANGED-AFTER-RECONCILIATION"
    await trust_roots.register(TrustRootDefinition.model_validate(changed), replace=True)

    candidate_id = reconciled.release_candidate.content.candidate_id
    result = await structured.process(candidate_id)
    repeated = await structured.process(candidate_id)

    assert result == repeated
    assert result.state is StructuredRunState.VALIDATED
    assert result.report.content.promotion_eligible is True
    assert result.report.content.resource_count == 2
    assert result.report.content.narrative_count == 2
    assert result.report.content.blockers == ()
    assert result.report.content.processed_at == reconciled.release_candidate.content.created_at
    async with database.session() as session:
        runs = await session.scalar(select(func.count()).select_from(StructuredPackageRunRow))
        resources = await session.scalar(
            select(func.count()).select_from(StructuredFHIRResourceRow)
        )
        narratives = await session.scalar(
            select(func.count()).select_from(StructuredNarrativeLinkRow)
        )
        input_runs = await session.scalar(select(func.count()).select_from(StructuredInputRunRow))
        narrative_artifacts = await session.scalar(
            select(func.count()).select_from(StructuredNarrativeArtifactRow)
        )
    assert runs == 1
    assert resources == 2
    assert narratives == 1
    assert input_runs == 1
    assert narrative_artifacts == 1
    await database.close()


async def test_structured_pipeline_rejects_tampered_input_closure_signature(
    tmp_path,
) -> None:
    database, _, reconciliation, inputs, structured = await build_pipeline(tmp_path, fhir_package())
    reconciled = await reconciliation.reconcile(
        "STRUCTURED_TEST", idempotency_key="tampered-closure-attestation"
    )
    assert reconciled.release_candidate is not None
    resolved = await inputs.resolve(reconciled.release_candidate.content.candidate_id)
    async with database.session() as session:
        row = await session.get(CryptographicAttestationRow, resolved.attestation.attestation_id)
        assert row is not None
        row.signature_base64 = base64.b64encode(b"\0" * 64).decode("ascii")

    with pytest.raises(RuntimeError, match="signature"):
        await structured.process(reconciled.release_candidate.content.candidate_id)

    async with database.session() as session:
        runs = await session.scalar(select(func.count()).select_from(StructuredPackageRunRow))
    assert runs == 0
    await database.close()


async def test_structured_pipeline_preserves_inventory_but_blocks_unsafe_authority(
    tmp_path,
) -> None:
    package = fhir_package(
        experimental=True,
        license_id="WRONG-LICENSE",
        include_narrative_authority=False,
        dependencies={"hl7.fhir.r4.core": "4.0.1"},
    )
    database, _, reconciliation, inputs, structured = await build_pipeline(tmp_path, package)
    reconciled = await reconciliation.reconcile(
        "STRUCTURED_TEST", idempotency_key="structured-blocked"
    )
    assert reconciled.release_candidate is not None
    resolved = await inputs.resolve(reconciled.release_candidate.content.candidate_id)
    assert resolved.state is StructuredInputState.RESOLVED
    assert len(resolved.report.content.dependency_packages) == 1

    result = await structured.process(reconciled.release_candidate.content.candidate_id)

    assert result.state is StructuredRunState.BLOCKED
    assert result.report.content.resource_count == 2
    assert result.report.content.resolved_dependency_count == 1
    assert result.report.content.narrative_artifact_count == 1
    assert result.report.content.promotion_eligible is False
    assert set(result.report.content.blockers) == {
        "CONTROLLING_NARRATIVE_NOT_DECLARED",
        "IMPLEMENTATION_GUIDE_EXPERIMENTAL",
        "LICENSE_DECLARATION_CONFLICT",
    }
    await database.close()


async def test_dependency_closure_resolves_transitive_patch_range(tmp_path) -> None:
    parent = dependency_package("example.parent", "1.0.0", {"example.child": "2.1.x"})
    older_child = dependency_package("example.child", "2.1.2")
    newest_child = dependency_package("example.child", "2.1.4")
    root = fhir_package(dependencies={"example.parent": "1.0.0"})
    database, _, reconciliation, inputs, _ = await build_pipeline(
        tmp_path,
        root,
        {
            ("example.parent", "1.0.0"): parent,
            ("example.child", "2.1.2"): older_child,
            ("example.child", "2.1.4"): newest_child,
        },
    )
    reconciled = await reconciliation.reconcile(
        "STRUCTURED_TEST", idempotency_key="transitive-range"
    )
    assert reconciled.release_candidate is not None

    result = await inputs.resolve(reconciled.release_candidate.content.candidate_id)

    assert result.state is StructuredInputState.RESOLVED
    assert {
        (item.package_id, item.version) for item in result.report.content.dependency_packages
    } == {("example.parent", "1.0.0"), ("example.child", "2.1.4")}
    assert len(result.report.content.requirements) == 2
    async with database.session() as session:
        dependency_rows = await session.scalar(
            select(func.count()).select_from(StructuredDependencyPackageRow)
        )
    assert dependency_rows == 2
    await database.close()


async def test_dependency_closure_accounts_for_multiple_pinned_versions(
    tmp_path,
) -> None:
    packages = {
        ("example.parenta", "1.0.0"): dependency_package(
            "example.parenta", "1.0.0", {"example.shared": "1.0.0"}
        ),
        ("example.parentb", "1.0.0"): dependency_package(
            "example.parentb", "1.0.0", {"example.shared": "2.0.0"}
        ),
        ("example.shared", "1.0.0"): dependency_package("example.shared", "1.0.0"),
        ("example.shared", "2.0.0"): dependency_package("example.shared", "2.0.0"),
    }
    database, _, reconciliation, inputs, _ = await build_pipeline(
        tmp_path,
        fhir_package(
            dependencies={
                "example.parenta": "1.0.0",
                "example.parentb": "1.0.0",
            }
        ),
        packages,
    )
    reconciled = await reconciliation.reconcile(
        "STRUCTURED_TEST", idempotency_key="multiple-pinned-versions"
    )
    assert reconciled.release_candidate is not None

    result = await inputs.resolve(reconciled.release_candidate.content.candidate_id)

    assert result.state is StructuredInputState.RESOLVED
    assert {
        (item.package_id, item.version) for item in result.report.content.dependency_packages
    } == set(packages)
    assert not any(
        issue.reason_code == "DEPENDENCY_VERSION_CONFLICT" for issue in result.report.content.issues
    )
    await database.close()


async def test_dependency_closure_blocks_registry_digest_drift(tmp_path) -> None:
    package_id = "example.parent"
    version = "1.0.0"
    artifact = dependency_package(package_id, version)
    database, _, reconciliation, inputs, _ = await build_pipeline(
        tmp_path,
        fhir_package(dependencies={package_id: version}),
        {(package_id, version): artifact},
        {(package_id, version): "0" * 40},
    )
    reconciled = await reconciliation.reconcile(
        "STRUCTURED_TEST", idempotency_key="registry-digest-drift"
    )
    assert reconciled.release_candidate is not None

    result = await inputs.resolve(reconciled.release_candidate.content.candidate_id)

    assert result.state is StructuredInputState.BLOCKED
    assert result.report.content.dependency_packages == ()
    assert {(issue.subject, issue.reason_code) for issue in result.report.content.issues} == {
        (
            "example.parent#1.0.0",
            "DEPENDENCY_REGISTRY_DIGEST_MISMATCH",
        )
    }
    assert "DEPENDENCY_CLOSURE_INCOMPLETE" in result.report.content.blockers
    assert result.attestation.statement_sha256
    await database.close()


async def test_dependency_closure_detects_package_cycle(tmp_path) -> None:
    parent = dependency_package("example.parent", "1.0.0", {"example.child": "1.0.0"})
    child = dependency_package("example.child", "1.0.0", {"example.parent": "1.0.0"})
    database, _, reconciliation, inputs, _ = await build_pipeline(
        tmp_path,
        fhir_package(dependencies={"example.parent": "1.0.0"}),
        {
            ("example.parent", "1.0.0"): parent,
            ("example.child", "1.0.0"): child,
        },
    )
    reconciled = await reconciliation.reconcile(
        "STRUCTURED_TEST", idempotency_key="dependency-cycle"
    )
    assert reconciled.release_candidate is not None

    result = await inputs.resolve(reconciled.release_candidate.content.candidate_id)

    assert result.state is StructuredInputState.BLOCKED
    assert len(result.report.content.dependency_packages) == 2
    assert any(issue.reason_code == "DEPENDENCY_CYCLE" for issue in result.report.content.issues)
    assert result.attestation.statement_sha256
    await database.close()


async def test_malformed_reconciled_package_produces_signed_blocked_report(
    tmp_path,
) -> None:
    database, _, reconciliation, inputs, structured = await build_pipeline(
        tmp_path, b"not-a-gzip-package"
    )
    reconciled = await reconciliation.reconcile(
        "STRUCTURED_TEST", idempotency_key="structured-malformed"
    )
    assert reconciled.release_candidate is not None
    resolved = await inputs.resolve(reconciled.release_candidate.content.candidate_id)
    assert resolved.state is StructuredInputState.BLOCKED

    result = await structured.process(reconciled.release_candidate.content.candidate_id)

    assert result.state is StructuredRunState.BLOCKED
    assert result.report.content.resource_count == 0
    assert result.report.content.blockers == ("ARCHIVE_NOT_GZIP:test.fhir#1.0.0",)
    assert result.attestation.statement_sha256
    await database.close()


async def test_materialization_accepts_experimental_companion_as_structural_only(
    tmp_path,
) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "The WHO DAK is the controlling clinical source.")
    narrative_content = pdf.tobytes()
    pdf.close()

    package = fhir_package(
        experimental=True,
        license_id="CC-BY-SA-3.0",
        include_narrative_authority=False,
    )
    database, trust_roots, reconciliation, inputs, structured = await build_pipeline(
        tmp_path,
        package,
        narrative_content=narrative_content,
    )
    reconciled = await reconciliation.reconcile(
        "STRUCTURED_TEST", idempotency_key="phase-3-materialization"
    )
    assert reconciled.release_candidate is not None
    candidate_id = reconciled.release_candidate.content.candidate_id
    closure = await inputs.resolve(candidate_id)
    assert closure.state is StructuredInputState.RESOLVED
    companion = await structured.process(candidate_id)
    assert companion.state is StructuredRunState.BLOCKED
    assert companion.report.content.blockers == (
        "CONTROLLING_NARRATIVE_NOT_DECLARED",
        "IMPLEMENTATION_GUIDE_EXPERIMENTAL",
        "LICENSE_DECLARATION_CONFLICT",
    )
    await trust_roots.register(
        structured_definition(package, per_asset_licensing=True), replace=True
    )

    signer = Ed25519Signer.from_pem(
        tmp_path / "private.pem",
        key_id="structured-test-key",
        signer_identity="structured-test-steward",
    )
    materializer = MaterializationService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        repository=SQLMaterializationRepository(database),
        ledger=SQLReconciliationLedger(database),
        attestations=SQLAttestationRepository(database),
        artifacts=ImmutableStewardArtifactStore(tmp_path / "artifacts"),
        extractor=DAKSourceExtractor(),
        signer=signer,
    )
    result = await materializer.materialize(candidate_id)
    repeated = await materializer.materialize(candidate_id)

    assert repeated == result
    assert result.state is MaterializationState.READY_FOR_QA
    assert result.report.content.ready_for_qa is True
    assert (
        result.report.content.authority_binding.content.trust_root_sha256
        != result.report.content.authority_binding.content.licensing_trust_root_sha256
    )
    assert result.report.content.coverage.complete is True
    assert len(result.report.content.evidence) == 1
    assert result.corpus_release_candidate is not None
    assert result.corpus_release_candidate.content.qa_required is True
    assert result.corpus_release_candidate.content.retrieval_eligible is False
    assets = result.report.content.authority_binding.content.assets
    controlling = [
        item for item in assets if item.authority_role is AuthorityRole.CONTROLLING_CLINICAL_SOURCE
    ]
    structured_assets = [
        item for item in assets if item.authority_role is AuthorityRole.STRUCTURED_COMPANION
    ]
    assert len(controlling) == 1
    assert controlling[0].clinical_content_promotable is True
    assert len(structured_assets) == 1
    assert structured_assets[0].experimental is True
    assert structured_assets[0].clinical_content_promotable is False
    evidence_entry = result.report.content.evidence[0]
    assert evidence_entry.artifact_sha256
    async with database.session() as session:
        runs = await session.scalar(select(func.count()).select_from(MaterializationRunRow))
        evidence_rows = await session.scalar(
            select(func.count()).select_from(MaterializedEvidenceRow)
        )
        materialized_row = await session.scalar(select(MaterializedEvidenceRow))
    assert runs == 1
    assert evidence_rows == 1
    assert materialized_row is not None
    assert materialized_row.payload["content"]["content_exact"] == (
        "The WHO DAK is the controlling clinical source."
    )

    qa_service = QAService(
        repository=SQLQARepository(database),
        releases=SQLCorpusReleaseRepository(database),
        trust_roots=trust_roots,
        attestations=SQLAttestationRepository(database),
        ledger=SQLReconciliationLedger(database),
        artifacts=ImmutableStewardArtifactStore(tmp_path / "artifacts"),
        extractor=DAKSourceExtractor(),
        signer=signer,
    )
    corpus_candidate_id = result.corpus_release_candidate.content.corpus_release_candidate_id
    completed = await qa_service.qa(corpus_candidate_id)

    assert completed.state is QARunState.VALIDATED
    assert completed.approved_count == 1
    assert completed.quarantined_count == 0
    assert completed.corpus_release_bundle is not None
    assert completed.corpus_release_bundle.activation_blockers() == ()
    release_id = completed.corpus_release_bundle.manifest.content.corpus_release_id
    async with database.session() as session:
        qa_runs = await session.scalar(select(func.count()).select_from(CorpusQARunRow))
        replay_rows = await session.scalar(
            select(func.count()).select_from(EvidenceAnchorReplayRow)
        )
        decisions = await session.scalar(select(func.count()).select_from(EvidenceQADecisionRow))
        canonical = await session.scalar(select(func.count()).select_from(CanonicalEvidenceRow))
        release = await session.get(CorpusReleaseRow, release_id)
    assert qa_runs == replay_rows == decisions == canonical == 1
    assert release is not None
    assert release.state == ReleaseState.VALIDATED.value
    assert release.index_status == "NOT_BUILT"
    await database.close()
