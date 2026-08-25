import asyncio
import json
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.corpus_steward.connectors import HTTPConnectorTransport
from app.corpus_steward.connectors.who_smart import WHOSmartFHIRConnector
from app.corpus_steward.crypto import Ed25519Signer, generate_ed25519_key_pair
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.schemas import (
    ArtifactKind,
    AttestationPurpose,
    CoverageExceptionContent,
    InventoryChangeKind,
    JobState,
    ReconciliationStage,
    TrustRootDefinition,
)
from app.corpus_steward.service import ReconciliationService
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.persistence.database import Database
from app.persistence.models import (
    CryptographicAttestationRow,
    ReconciliationJobAttemptRow,
    StewardArtifactRow,
)
from app.schemas.domain import utc_now

ROOT = Path(__file__).parents[4]
SYNTHETIC_ROOT = ROOT / "data" / "fixtures" / "trust-root-synthetic.json"
WHO_ROOT = ROOT / "data" / "trust-roots" / "who-smart-hiv.json"


async def build_service(tmp_path, definition: TrustRootDefinition):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'steward.sqlite3'}")
    await database.create_schema_for_tests()
    private_pem, _ = generate_ed25519_key_pair()
    private_path = tmp_path / "stage-private.pem"
    private_path.write_bytes(private_pem)
    signer = Ed25519Signer.from_pem(
        private_path,
        key_id="local-steward-stage",
        signer_identity="test-steward",
    )
    attestations = SQLAttestationRepository(database)
    await attestations.register_key(
        key_id=signer.key_id,
        signer_identity=signer.signer_identity,
        public_key_pem=signer.public_key_pem(),
        purposes=(AttestationPurpose.STAGE, AttestationPurpose.EXCEPTION),
    )
    trust_roots = SQLTrustRootRegistry(database)
    await trust_roots.register(definition)
    service = ReconciliationService(
        trust_roots=trust_roots,
        ledger=SQLReconciliationLedger(database),
        attestations=attestations,
        artifacts=ImmutableStewardArtifactStore(tmp_path / "artifacts"),
        transport=HTTPConnectorTransport(
            max_bytes=1024 * 1024,
            timeout_seconds=5,
            allow_private_networks=True,
        ),
        signer=signer,
    )
    return database, service, trust_roots


def synthetic_definition() -> TrustRootDefinition:
    return TrustRootDefinition.model_validate_json(
        SYNTHETIC_ROOT.read_text(encoding="utf-8")
    )


async def test_synthetic_reconciliation_is_complete_attested_and_idempotent(
    tmp_path,
) -> None:
    database, service, _ = await build_service(tmp_path, synthetic_definition())

    report = await service.reconcile("SYNTHETIC", idempotency_key="poll-001")
    repeated = await service.reconcile("SYNTHETIC", idempotency_key="poll-001")

    assert report.state is JobState.COMPLETED
    assert report.inventory_count == 2
    assert report.new_count == 2
    assert report.included_count == 2
    assert report.blockers == ()
    assert report.release_candidate is not None
    assert repeated == report
    assert report.release_candidate.content.snapshot.complete is True
    assert len(report.release_candidate.content.stage_attestations) == 4
    async with database.session() as session:
        attempts = await session.scalar(
            select(func.count()).select_from(ReconciliationJobAttemptRow)
        )
        attestations = await session.scalar(
            select(func.count()).select_from(CryptographicAttestationRow)
        )
        artifacts = await session.scalar(
            select(func.count()).select_from(StewardArtifactRow)
        )
    assert attempts == 1
    assert attestations == 5
    assert artifacts == 6  # two raw pages, manifest, two sources, candidate
    await database.close()


async def test_artifact_registry_is_idempotent_under_concurrent_discovery(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'artifacts.sqlite3'}")
    await database.create_schema_for_tests()
    artifact = ImmutableStewardArtifactStore(tmp_path / "artifacts").put(
        b"shared immutable dependency metadata",
        media_type="application/json",
        kind=ArtifactKind.DEPENDENCY_METADATA,
    )
    ledger = SQLReconciliationLedger(database)

    await asyncio.gather(*(ledger.record_artifact(artifact) for _ in range(12)))

    async with database.session() as session:
        count = await session.scalar(select(func.count()).select_from(StewardArtifactRow))
    assert count == 1
    await database.close()


async def test_reconciliation_detects_new_changed_and_removed_inventory(tmp_path) -> None:
    original = synthetic_definition()
    database, service, trust_roots = await build_service(tmp_path, original)
    first = await service.reconcile("SYNTHETIC", idempotency_key="poll-001")
    assert first.state is JobState.COMPLETED

    payload = original.model_dump(mode="json")
    payload["connector_config"]["items"] = [
        {
            "content": "synthetic-guideline-b-v3",
            "item_id": "SYNTH_B",
            "title": "Synthetic Guideline B",
            "version": "3",
        },
        {
            "content": "synthetic-guideline-c-v1",
            "item_id": "SYNTH_C",
            "title": "Synthetic Guideline C",
            "version": "1",
        },
    ]
    changed_root = TrustRootDefinition.model_validate(payload)
    await trust_roots.register(changed_root, replace=True)

    second = await service.reconcile("SYNTHETIC", idempotency_key="poll-002")

    assert second.state is JobState.COMPLETED
    assert second.new_count == 1
    assert second.changed_count == 1
    assert second.removed_count == 1
    assert second.release_candidate is not None
    changes = {
        change.item_id: change.change
        for change in second.release_candidate.content.changes
    }
    assert changes == {
        "SYNTH_A": InventoryChangeKind.REMOVED,
        "SYNTH_B": InventoryChangeKind.CHANGED,
        "SYNTH_C": InventoryChangeKind.NEW,
    }
    await database.close()


async def test_unchanged_inventory_reuses_conditionally_fetched_artifacts(tmp_path) -> None:
    database, service, _ = await build_service(tmp_path, synthetic_definition())
    await service.reconcile("SYNTHETIC", idempotency_key="poll-001")

    second = await service.reconcile("SYNTHETIC", idempotency_key="poll-002")

    assert second.state is JobState.COMPLETED
    assert second.new_count == 0
    assert second.changed_count == 0
    assert second.removed_count == 0
    assert second.release_candidate is not None
    assert all(
        change.change is InventoryChangeKind.UNCHANGED
        for change in second.release_candidate.content.changes
    )
    assert all(
        artifact.reused_after_not_modified
        for artifact in second.release_candidate.content.source_artifacts
    )
    await database.close()


async def test_changed_bytes_override_unchanged_inventory_metadata(tmp_path) -> None:
    payload = synthetic_definition().model_dump(mode="json")
    payload["connector_config"]["items"] = [
        {
            "content": "publisher-bytes-v1",
            "item_id": "SYNTH_A",
            "publisher_metadata": {"opaque_revision": "same"},
            "title": "Synthetic Guideline A",
            "version": "1",
        }
    ]
    first_root = TrustRootDefinition.model_validate(payload)
    database, service, trust_roots = await build_service(tmp_path, first_root)
    await service.reconcile("SYNTHETIC", idempotency_key="poll-001")

    payload["connector_config"]["items"][0]["content"] = "publisher-bytes-v2"
    await trust_roots.register(TrustRootDefinition.model_validate(payload), replace=True)
    second = await service.reconcile("SYNTHETIC", idempotency_key="poll-002")

    assert second.new_count == 0
    assert second.changed_count == 1
    assert second.release_candidate is not None
    assert second.release_candidate.content.changes[0].change is InventoryChangeKind.CHANGED
    await database.close()


async def test_fetch_failure_blocks_until_covered_by_verified_exception(tmp_path) -> None:
    payload = synthetic_definition().model_dump(mode="json")
    payload["connector_config"]["fail_artifact_item_ids"] = ["SYNTH_B"]
    definition = TrustRootDefinition.model_validate(payload)
    database, service, _ = await build_service(tmp_path, definition)

    blocked = await service.reconcile("SYNTHETIC", idempotency_key="poll-blocked")
    assert blocked.state is JobState.BLOCKED
    assert blocked.release_candidate is None
    assert blocked.blockers[0].startswith("UNACCOUNTED_INVENTORY_ITEM:SYNTH_B")

    await service.approve_exception(
        CoverageExceptionContent(
            trust_root_id="SYNTHETIC",
            inventory_item_id="SYNTH_B",
            reason="ACQUISITION",
            details="Deterministic injected publisher failure for gate coverage.",
            approved_at=utc_now(),
        )
    )
    covered = await service.reconcile("SYNTHETIC", idempotency_key="poll-covered")

    assert covered.state is JobState.COMPLETED
    assert covered.included_count == 1
    assert covered.excepted_count == 1
    assert covered.release_candidate is not None
    assert covered.release_candidate.content.snapshot.excepted_item_ids == ("SYNTH_B",)
    await database.close()


async def test_failed_job_attempt_resumes_from_first_incomplete_stage(tmp_path) -> None:
    database, _, _ = await build_service(tmp_path, synthetic_definition())
    ledger = SQLReconciliationLedger(database)
    first = await ledger.start_job(synthetic_definition(), idempotency_key="resume-me")
    await ledger.begin_stage(first.job_id, ReconciliationStage.ENUMERATE_INVENTORY)
    await ledger.fail_stage(
        first.job_id,
        first.attempt_id,
        ReconciliationStage.ENUMERATE_INVENTORY,
        "injected crash",
    )

    resumed = await ledger.start_job(synthetic_definition(), idempotency_key="resume-me")

    assert resumed.attempt_number == 2
    assert resumed.resumed_from_stage is ReconciliationStage.ENUMERATE_INVENTORY
    await database.close()


async def test_who_connector_maps_only_current_official_release() -> None:
    definition = TrustRootDefinition.model_validate_json(WHO_ROOT.read_text(encoding="utf-8"))
    package_list = {
        "list": [
            {
                "version": "current",
                "path": "http://worldhealthorganization.github.io/smart-hiv",
                "status": "ci-build",
                "sequence": "ci-build",
            },
            {
                "version": "1.0.0",
                "path": "http://smart.who.int/hiv/v1.0.0",
                "status": "release",
                "sequence": "Releases",
                "fhirversion": "4.0.1",
                "date": "2025-07-08",
                "current": True,
            },
        ]
    }

    async def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(package_list).encode(),
            request=request,
        )

    transport = HTTPConnectorTransport(
        max_bytes=1024 * 1024,
        timeout_seconds=5,
        allow_private_networks=True,
        transport=httpx.MockTransport(response),
    )
    inventory = await WHOSmartFHIRConnector().enumerate_inventory(definition, transport)

    assert [item.item_id for item in inventory.items] == ["smart.who.int.hiv#1.0.0"]
    assert inventory.items[0].artifact_url == "https://smart.who.int/hiv/package.tgz"
    assert len(inventory.responses) == 1
