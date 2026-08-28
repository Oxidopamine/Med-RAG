"""The narrative-anchored input closure: a PDF-only publisher reaching a signed closure.

`StructuredInputClosureService.resolve` parses the preserved source artifact as a FHIR
package unconditionally, so a PDF-only publisher blocks three times over on the DAK path:
the parse raises, `SOURCE_PACKAGE` becomes a BLOCK, and the closure is incomplete before
materialization is ever reached. Work item 4 of docs/narrative-only-materialization.md adds
a topology switch instead of a bypass.

These tests pin the two properties that make the switch safe rather than convenient:

* the narrative artifact is **synthesized from the already-preserved source bytes**, never
  refetched, so one document does not acquire two provenance stories; and
* declaring both topologies' configuration is refused rather than silently merged.

The DAK path's own behaviour is covered by `test_structured_fhir_pipeline.py` and is
deliberately untouched here - a trust root with no `source_topology` still resolves exactly
as it did before.
"""

import base64
import hashlib
from pathlib import Path

import httpx
import pymupdf
import pytest
from sqlalchemy import func, select

from app.corpus_steward.connectors import HTTPConnectorTransport
from app.corpus_steward.crypto import Ed25519Signer, generate_ed25519_key_pair
from app.corpus_steward.fhir_package import FHIRPackageParser
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.schemas import AttestationPurpose, TrustRootDefinition
from app.corpus_steward.service import ReconciliationService
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import SQLStructuredInputRepository
from app.corpus_steward.structured_input_schemas import StructuredInputState
from app.corpus_steward.structured_input_service import StructuredInputClosureService
from app.corpus_steward.structured_repository import SQLStructuredPackageRepository
from app.persistence.database import Database
from app.persistence.models import (
    StructuredInputRunRow,
    StructuredNarrativeArtifactRow,
)


def _pdf(text: str = "Consolidated guideline recommendation text.") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 96), text)
    payload = document.tobytes()
    document.close()
    return payload


def narrative_definition(pdf_bytes: bytes) -> TrustRootDefinition:
    """A publisher whose inventory source artifact *is* the clinical narrative.

    The inverse of the DAK fixture: no FHIR package, no controlling-narrative block, and
    no dependency registry. `asset_id == item_id` is the topology rather than a shortcut,
    so the licensing policy is keyed by the inventory item identifier.
    """

    return TrustRootDefinition.model_validate(
        {
            "trust_root_id": "NARRATIVE_TEST",
            "publisher_id": "PUB_NARRATIVE",
            "publisher_name": "Narrative Publisher",
            "allowed_domains": ["fixtures.invalid"],
            "jurisdictions": ["WORLD"],
            "product_families": ["CLINICAL_GUIDELINE"],
            "polling_interval_seconds": 3600,
            "connector_name": "synthetic",
            "connector_version": "1.0.0",
            "connector_config": {
                "source_topology": "NARRATIVE_ANCHORED",
                "structured_asset_id": "NARRATIVE_GUIDELINE",
                "items": [
                    {
                        "item_id": "NARRATIVE_GUIDELINE",
                        "title": "Consolidated guideline",
                        "version": "1",
                        "lifecycle_status": "EFFECTIVE",
                        "media_type": "application/pdf",
                        "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
                    }
                ],
            },
            "asset_licensing": [
                {
                    "asset_id": "NARRATIVE_GUIDELINE",
                    "asset_kind": "NARRATIVE_SOURCE",
                    "license_id": "CC-BY-NC-SA-3.0-IGO",
                    "policy_url": "https://fixtures.invalid/narrative-license",
                    "acquisition_allowed": True,
                    "evidence_materialization_allowed": True,
                }
            ],
            "trusted_stage_key_ids": ["narrative-test-key"],
        }
    )


async def build_narrative_pipeline(tmp_path: Path, pdf_bytes: bytes):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'narrative.sqlite3'}")
    await database.create_schema_for_tests()
    private_pem, _ = generate_ed25519_key_pair()
    private_path = tmp_path / "private.pem"
    private_path.write_bytes(private_pem)
    signer = Ed25519Signer.from_pem(
        private_path,
        key_id="narrative-test-key",
        signer_identity="narrative-test-steward",
    )
    attestations = SQLAttestationRepository(database)
    await attestations.register_key(
        key_id=signer.key_id,
        signer_identity=signer.signer_identity,
        public_key_pem=signer.public_key_pem(),
        purposes=(AttestationPurpose.STAGE,),
    )
    trust_roots = SQLTrustRootRegistry(database)
    await trust_roots.register(narrative_definition(pdf_bytes))
    ledger = SQLReconciliationLedger(database)
    artifacts = ImmutableStewardArtifactStore(tmp_path / "artifacts")

    async def response(request: httpx.Request) -> httpx.Response:
        # Any outbound request at all is a failure of the no-refetch property.
        raise AssertionError(f"narrative closure must not fetch: {request.url}")

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
    inputs = StructuredInputClosureService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        parser=FHIRPackageParser(),
        transport=transport,
        signer=signer,
    )
    return database, trust_roots, reconciliation, inputs


async def test_narrative_anchored_closure_resolves_without_a_fhir_package(tmp_path) -> None:
    pdf_bytes = _pdf()
    database, _, reconciliation, inputs = await build_narrative_pipeline(tmp_path, pdf_bytes)
    reconciled = await reconciliation.reconcile(
        "NARRATIVE_TEST", idempotency_key="narrative-valid"
    )
    assert reconciled.release_candidate is not None
    candidate_id = reconciled.release_candidate.content.candidate_id

    resolved = await inputs.resolve(candidate_id)
    repeated = await inputs.resolve(candidate_id)

    assert resolved.state is StructuredInputState.RESOLVED
    assert repeated == resolved, "the narrative closure must be idempotent like the DAK one"

    content = resolved.report.content
    assert content.complete is True
    assert content.blockers == ()

    # The narrative *is* the source artifact: same digest, no second acquisition.
    assert len(content.narrative_artifacts) == 1
    narrative = content.narrative_artifacts[0]
    assert narrative.asset_id == "NARRATIVE_GUIDELINE"
    assert narrative.artifact_sha256 == content.source_artifact_sha256
    assert narrative.artifact_sha256 == hashlib.sha256(pdf_bytes).hexdigest()
    assert content.expected_narrative_asset_ids == ("NARRATIVE_GUIDELINE",)

    # No dependency graph exists, so none can be incomplete.
    assert content.direct_dependencies == ()
    assert content.dependency_packages == ()
    assert content.dependency_inventory_sha256 is None

    outcomes = {check.code.value: check.outcome.value for check in content.checks}
    assert outcomes["SOURCE_PACKAGE"] == "PASS"
    assert outcomes["DEPENDENCY_CLOSURE"] == "PASS"
    assert outcomes["NARRATIVE_ASSETS"] == "PASS"

    async with database.session() as session:
        input_runs = await session.scalar(select(func.count()).select_from(StructuredInputRunRow))
        narrative_rows = await session.scalar(
            select(func.count()).select_from(StructuredNarrativeArtifactRow)
        )
    assert input_runs == 1
    assert narrative_rows == 1
    await database.close()


async def test_narrative_anchored_trust_root_rejects_dak_only_configuration(tmp_path) -> None:
    """Declaring both topologies' configuration is a contradiction, not a merge."""

    pdf_bytes = _pdf("Second guideline.")
    payload = narrative_definition(pdf_bytes).model_dump(mode="json")
    payload["connector_config"]["dependency_registry"] = {
        "base_url": "https://fixtures.invalid/packages",
        "allowed_domains": ["fixtures.invalid"],
        "max_packages": 20,
        "max_depth": 5,
        "max_total_bytes": 10485760,
        "concurrency": 2,
    }
    database, trust_roots, reconciliation, inputs = await build_narrative_pipeline(
        tmp_path, pdf_bytes
    )
    await trust_roots.register(TrustRootDefinition.model_validate(payload), replace=True)
    reconciled = await reconciliation.reconcile(
        "NARRATIVE_TEST", idempotency_key="narrative-contradictory"
    )
    assert reconciled.release_candidate is not None

    with pytest.raises(ValueError, match="must not declare dependency_registry"):
        await inputs.resolve(reconciled.release_candidate.content.candidate_id)
    await database.close()


async def test_truncated_narrative_bytes_block_the_closure(tmp_path) -> None:
    """A signed RESOLVED closure must not assert that broken bytes are the authority.

    The DAK path validates narrative content before storing it. The narrative path has to
    do the same on the source artifact, or a truncated or mistyped document still produces
    a signed closure calling itself the controlling clinical narrative.
    """

    truncated = _pdf("Guidance.")[:-64]  # drop the %%EOF terminator
    database, _, reconciliation, inputs = await build_narrative_pipeline(tmp_path, truncated)
    reconciled = await reconciliation.reconcile(
        "NARRATIVE_TEST", idempotency_key="narrative-truncated"
    )
    assert reconciled.release_candidate is not None

    resolved = await inputs.resolve(reconciled.release_candidate.content.candidate_id)
    content = resolved.report.content
    assert resolved.state is StructuredInputState.BLOCKED
    assert content.complete is False
    outcomes = {check.code.value: check.outcome.value for check in content.checks}
    assert outcomes["NARRATIVE_ASSETS"] == "BLOCK"
    assert any(issue.reason_code == "NARRATIVE_CONTENT_INVALID" for issue in content.issues)
    await database.close()


async def test_unlicensed_asset_never_reaches_a_closure(tmp_path) -> None:
    """An asset the trust root does not license cannot become a controlling narrative.

    Reconciliation refuses first, so the closure's own licence check never fires on this
    path - the closure re-reads the *pinned* trust-root revision, which cannot have changed
    since reconciliation checked it. That check is kept as defence in depth (it turns a
    raw ValueError into a signed BLOCK if a future topology ever separates the inventory
    item from the licensed asset id), but the property worth pinning is that the pipeline
    fails closed, not which stage catches it.
    """

    pdf_bytes = _pdf("Unlicensed guidance.")
    payload = narrative_definition(pdf_bytes).model_dump(mode="json")
    payload["asset_licensing"] = [
        {**payload["asset_licensing"][0], "asset_id": "SOME_OTHER_ASSET"}
    ]
    database, trust_roots, reconciliation, _ = await build_narrative_pipeline(
        tmp_path, pdf_bytes
    )
    await trust_roots.register(TrustRootDefinition.model_validate(payload), replace=True)

    with pytest.raises(ValueError, match="no unique licensing policy"):
        await reconciliation.reconcile(
            "NARRATIVE_TEST", idempotency_key="narrative-unlicensed"
        )
    await database.close()


async def test_unknown_source_topology_is_refused(tmp_path) -> None:
    """An unrecognised topology must fail closed, not fall back to the DAK path."""

    pdf_bytes = _pdf("Third guideline.")
    payload = narrative_definition(pdf_bytes).model_dump(mode="json")
    payload["connector_config"]["source_topology"] = "SOMETHING_ELSE"
    database, trust_roots, reconciliation, inputs = await build_narrative_pipeline(
        tmp_path, pdf_bytes
    )
    await trust_roots.register(TrustRootDefinition.model_validate(payload), replace=True)
    reconciled = await reconciliation.reconcile(
        "NARRATIVE_TEST", idempotency_key="narrative-unknown-topology"
    )
    assert reconciled.release_candidate is not None

    with pytest.raises(ValueError, match="unknown source_topology"):
        await inputs.resolve(reconciled.release_candidate.content.candidate_id)
    await database.close()
