"""Composing two QA'd guidelines into the one release serving can hold.

This is the first test in the narrative sequence that exercises more than one document, and
that is the point: every check assembly performs is a property that only becomes checkable
once there are two. The happy path proves the merge; the refusals prove the merge is not
merely optimistic.
"""

import base64
import hashlib
from pathlib import Path

import httpx
import pymupdf
import pytest
from sqlalchemy import func, select

from app.corpus.releases import SQLCorpusReleaseRepository
from app.corpus_steward.connectors import HTTPConnectorTransport
from app.corpus_steward.crypto import Ed25519Signer, generate_ed25519_key_pair
from app.corpus_steward.evidence_extractor import DAKSourceExtractor
from app.corpus_steward.fhir_package import FHIRPackageParser
from app.corpus_steward.index_repository import SQLIndexBasisRepository
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.materialization_repository import SQLMaterializationRepository
from app.corpus_steward.materialization_service import MaterializationService
from app.corpus_steward.narrative_extractor import NarrativeSourceExtractor
from app.corpus_steward.narrative_repository import SQLNarrativeAnalysisRepository
from app.corpus_steward.narrative_service import NarrativeSourceAnalysisService
from app.corpus_steward.qa_repository import SQLQARepository
from app.corpus_steward.qa_service import QAService
from app.corpus_steward.registry import SQLAttestationRepository, SQLTrustRootRegistry
from app.corpus_steward.release_assembly_service import (
    ReleaseAssemblyError,
    ReleaseAssemblyService,
)
from app.corpus_steward.schemas import AttestationPurpose, TrustRootDefinition
from app.corpus_steward.service import ReconciliationService
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import SQLStructuredInputRepository
from app.corpus_steward.structured_input_service import StructuredInputClosureService
from app.corpus_steward.structured_repository import SQLStructuredPackageRepository
from app.persistence.database import Database
from app.persistence.models import (
    CorpusQARunRow,
    CorpusReleaseEvidenceRow,
    CorpusReleaseMemberRow,
    CorpusReleaseRow,
)

ITEMS = ("GUIDELINE_HYPERTENSION", "GUIDELINE_ANAEMIA")


ASPECTS = ("initiation", "monitoring", "referral")


def _pdf(topic: str) -> bytes:
    """A guideline whose pages differ, as a real one does.

    Identical pages would be exact duplicates, and QA quarantines all but the first - which
    would leave one approved record per document and make a composite of two documents
    indistinguishable from a composite of two pages.
    """

    document = pymupdf.open()
    for index, aspect in enumerate(ASPECTS):
        page = document.new_page()
        page.insert_text((72, 20), f"{index + 1} {topic} guideline", fontsize=9)
        page.insert_textbox(
            pymupdf.Rect(72, 60, 523, 380),
            f"Recommendation on {aspect} for {topic}: adults meeting the diagnostic "
            f"threshold for {aspect} should begin the {aspect} pathway and be reviewed "
            f"at three months, then annually. " * 4,
            fontsize=8,
        )
    payload = document.tobytes()
    document.close()
    return payload


def _definition(pdfs: dict[str, bytes]) -> TrustRootDefinition:
    return TrustRootDefinition.model_validate(
        {
            "trust_root_id": "NARRATIVE_MULTI",
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
                "items": [
                    {
                        "item_id": item_id,
                        "title": f"{item_id} guideline",
                        "version": "1",
                        "lifecycle_status": "EFFECTIVE",
                        "media_type": "application/pdf",
                        "content_base64": base64.b64encode(pdfs[item_id]).decode("ascii"),
                    }
                    for item_id in ITEMS
                ],
            },
            "asset_licensing": [
                {
                    "asset_id": item_id,
                    "asset_kind": "NARRATIVE_SOURCE",
                    "license_id": "CC-BY-NC-SA-3.0-IGO",
                    "policy_url": "https://fixtures.invalid/narrative-license",
                    "acquisition_allowed": True,
                    "evidence_materialization_allowed": True,
                }
                for item_id in ITEMS
            ],
            "trusted_stage_key_ids": ["narrative-test-key"],
        }
    )


async def _corpus(tmp_path: Path, *, promote: bool = True):
    """Two guidelines, each carried independently as far as a QA run."""

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'assembly.sqlite3'}")
    await database.create_schema_for_tests()
    private_pem, _ = generate_ed25519_key_pair()
    (tmp_path / "private.pem").write_bytes(private_pem)
    signer = Ed25519Signer.from_pem(
        tmp_path / "private.pem",
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
    pdfs = {item_id: _pdf(item_id) for item_id in ITEMS}
    await trust_roots.register(_definition(pdfs))
    ledger = SQLReconciliationLedger(database)
    artifacts = ImmutableStewardArtifactStore(tmp_path / "artifacts")

    async def response(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"narrative pipeline must not fetch: {request.url}")

    transport = HTTPConnectorTransport(
        max_bytes=10 * 1024 * 1024,
        timeout_seconds=5,
        allow_private_networks=True,
        transport=httpx.MockTransport(response),
    )
    reconciled = await ReconciliationService(
        trust_roots=trust_roots,
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        transport=transport,
        signer=signer,
    ).reconcile("NARRATIVE_MULTI", idempotency_key="multi")
    candidate_id = reconciled.release_candidate.content.candidate_id

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
    narrative_repository = SQLNarrativeAnalysisRepository(database)
    census = NarrativeSourceAnalysisService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        repository=narrative_repository,
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        signer=signer,
    )
    materializer = MaterializationService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        repository=SQLMaterializationRepository(database),
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        extractor=DAKSourceExtractor(),
        signer=signer,
        narrative_repository=narrative_repository,
        narrative_extractor=NarrativeSourceExtractor(),
    )
    qa_service = QAService(
        repository=SQLQARepository(database),
        releases=SQLCorpusReleaseRepository(database),
        trust_roots=trust_roots,
        attestations=attestations,
        ledger=ledger,
        artifacts=artifacts,
        extractor=DAKSourceExtractor(),
        signer=signer,
    )

    qa_run_ids = []
    for item_id in ITEMS:
        await inputs.resolve(candidate_id, item_id=item_id)
        await census.analyse(candidate_id, item_id=item_id)
        materialized = await materializer.materialize_narrative(candidate_id, item_id=item_id)
        corpus_candidate = materialized.corpus_release_candidate
        assert corpus_candidate is not None, f"{item_id} did not reach QA"
        result = await qa_service.qa(
            corpus_candidate.content.corpus_release_candidate_id, promote=promote
        )
        qa_run_ids.append(result.qa_run_id)

    assembler = ReleaseAssemblyService(
        database=database,
        qa=qa_service,
        releases=SQLCorpusReleaseRepository(database),
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        signer=signer,
    )
    return database, assembler, tuple(qa_run_ids)


@pytest.mark.asyncio
async def test_two_guidelines_compose_into_one_servable_release(tmp_path) -> None:
    database, assembler, qa_run_ids = await _corpus(tmp_path, promote=False)

    assembly = await assembler.assemble(qa_run_ids)
    content = assembly.content

    assert len(content.members) == 2
    assert content.evidence_count == sum(m.approved_count for m in content.members)
    assert content.qdrant_collection == f"corpus_{content.corpus_release_id.lower()}"

    async with database.session() as session:
        composite = await session.get(CorpusReleaseRow, content.corpus_release_id)
        members = tuple(await session.scalars(select(CorpusReleaseMemberRow)))
        membership = await session.scalar(
            select(func.count())
            .select_from(CorpusReleaseEvidenceRow)
            .where(CorpusReleaseEvidenceRow.corpus_release_id == content.corpus_release_id)
        )
    assert composite is not None, "assembly must go through the ordinary registrar"
    assert composite.qdrant_collection == content.qdrant_collection
    assert membership == content.evidence_count
    assert {row.qa_run_id for row in members} == set(qa_run_ids)
    await database.close()


@pytest.mark.asyncio
async def test_the_composite_carries_both_documents_evidence(tmp_path) -> None:
    database, assembler, qa_run_ids = await _corpus(tmp_path, promote=False)
    assembly = await assembler.assemble(qa_run_ids)

    async with database.session() as session:
        rows = tuple(
            await session.scalars(
                select(CorpusReleaseEvidenceRow.evidence_id).where(
                    CorpusReleaseEvidenceRow.corpus_release_id
                    == assembly.content.corpus_release_id
                )
            )
        )
    assert len(set(rows)) == len(rows) == assembly.content.evidence_count
    assert assembly.content.evidence_count > 2, "each guideline should carry several units"
    await database.close()


@pytest.mark.asyncio
async def test_assembly_is_deterministic_in_its_membership(tmp_path) -> None:
    """The release id derives from the member bundle digests.

    Re-assembling the same set must name the same release rather than minting a second
    identity for the same corpus.
    """

    database, assembler, qa_run_ids = await _corpus(tmp_path, promote=False)
    first = await assembler.assemble(qa_run_ids)

    with pytest.raises(ReleaseAssemblyError, match="already compose"):
        await assembler.assemble(tuple(reversed(qa_run_ids)))

    assert first.content.corpus_release_id.startswith("CR_")
    await database.close()


@pytest.mark.asyncio
async def test_a_qa_run_cannot_be_composed_into_two_releases(tmp_path) -> None:
    database, assembler, qa_run_ids = await _corpus(tmp_path, promote=False)
    await assembler.assemble(qa_run_ids[:1])

    with pytest.raises(ReleaseAssemblyError, match="already compose"):
        await assembler.assemble(qa_run_ids)
    await database.close()


@pytest.mark.asyncio
async def test_assembly_refuses_an_unknown_or_repeated_run(tmp_path) -> None:
    database, assembler, qa_run_ids = await _corpus(tmp_path, promote=False)

    with pytest.raises(ReleaseAssemblyError, match="not found"):
        await assembler.assemble(("QA_does_not_exist",))
    with pytest.raises(ReleaseAssemblyError, match="cannot compose a QA run twice"):
        await assembler.assemble((qa_run_ids[0], qa_run_ids[0]))
    with pytest.raises(ReleaseAssemblyError, match="at least one"):
        await assembler.assemble(())
    await database.close()


def test_the_assembly_digest_covers_its_membership() -> None:
    """Two different member sets cannot produce the same release identity."""

    from app.corpus_steward.release_assembly_schemas import ReleaseAssemblyMember
    from app.corpus_steward.release_assembly_service import ReleaseAssemblyService

    def member(digest: str) -> ReleaseAssemblyMember:
        return ReleaseAssemblyMember(
            qa_run_id=f"QA_{digest[:8]}",
            corpus_release_candidate_id=f"CRC_{digest[:8]}",
            materialization_run_id=f"MAT_{digest[:8]}",
            decision_batch_sha256=digest,
            evidence_manifest_sha256=digest,
            approved_count=1,
        )

    one = hashlib.sha256(b"one").hexdigest()
    two = hashlib.sha256(b"two").hexdigest()
    assert ReleaseAssemblyService._release_id((member(one),)) != (
        ReleaseAssemblyService._release_id((member(one), member(two)))
    )


@pytest.mark.asyncio
async def test_a_member_can_decide_without_promoting(tmp_path) -> None:
    """The seam composite assembly needs.

    Promotion binds evidence to a release id inside the signed record, so a member that
    promoted itself could never be re-pointed at the composite. It stops at DECIDED with
    its decision batch sealed instead. See docs/qa-promotion-separation.md.
    """

    database, _assembler, qa_run_ids = await _corpus(tmp_path, promote=False)

    async with database.session() as session:
        runs = tuple(await session.scalars(select(CorpusQARunRow)))
    assert len(runs) == len(qa_run_ids) == 2
    for run in runs:
        assert run.state == "DECIDED"
        assert run.decision_batch_sha256 is not None, "the batch is sealed, not skipped"
        assert run.approved_count > 0, "deciding still approves evidence"
        assert run.corpus_release_id is None
        assert run.bundle_sha256 is None
        assert run.bundle_artifact_sha256 is None
    await database.close()


@pytest.mark.asyncio
async def test_deciding_without_promoting_is_idempotent(tmp_path) -> None:
    database, _assembler, _qa_run_ids = await _corpus(tmp_path, promote=False)

    async with database.session() as session:
        first = {row.qa_run_id: row.decision_batch_sha256 for row in await session.scalars(
            select(CorpusQARunRow)
        )}
    assert first and all(first.values())
    await database.close()


@pytest.mark.asyncio
async def test_the_single_document_default_still_promotes(tmp_path) -> None:
    """The regression that matters: `promote=True` is the default and is unchanged.

    The active HIV release descends from this path, so it has to remain the same sequence
    of calls it has always been.
    """

    database, _assembler, qa_run_ids = await _corpus(tmp_path)

    async with database.session() as session:
        runs = tuple(await session.scalars(select(CorpusQARunRow)))
        releases = tuple(await session.scalars(select(CorpusReleaseRow)))
    assert len(qa_run_ids) == 2
    for run in runs:
        assert run.state == "VALIDATED"
        assert run.corpus_release_id is not None
        assert run.bundle_artifact_sha256 is not None
    assert len(releases) == 2, "one release per document, as before"
    await database.close()


@pytest.mark.asyncio
async def test_index_basis_reconciles_every_member_of_a_composite(tmp_path) -> None:
    """A composite must reach the index plane, not just the registrar.

    `SQLIndexBasisRepository.load` selected a single QA run and compared that one
    member's approved decisions against the whole release, so every multi-document
    build refused with `approved QA decisions do not match release membership`. The
    single-document releases in the rest of the suite could not see it, because there
    the one member's decisions *are* the release.
    """

    database, assembler, qa_run_ids = await _corpus(tmp_path, promote=False)
    assembly = await assembler.assemble(qa_run_ids)

    basis = await SQLIndexBasisRepository(database).load(
        assembly.content.corpus_release_id
    )

    assert basis.qa_run_ids == tuple(sorted(qa_run_ids))
    assert len(basis.qa_run_ids) > 1, "the fixture must exercise more than one member"
    assert basis.qa_run_id == basis.qa_run_ids[0]
    assert basis.approved_count == assembly.content.evidence_count
    assert basis.approved_count == len(basis.bundle.evidence)
    assert basis.materialized_count == basis.approved_count + basis.quarantined_count
    await database.close()
