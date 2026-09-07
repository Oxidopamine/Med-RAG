"""Narrative materialization: a guideline PDF becoming source-anchored evidence.

The property that matters here is the one the census exists to provide. Materialization
recomputes the unit inventory from the bytes it actually extracts and must agree with the
signed prior statement, or refuse to promote. A test suite that only covered the happy path
would pass just as well against a rubber stamp, so the disagreement case is the centre of
this file.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.evidence_extractor import DAKSourceExtractor
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.materialization_repository import SQLMaterializationRepository
from app.corpus_steward.materialization_schemas import (
    NARRATIVE_MATERIALIZER_NAME,
    NARRATIVE_MATERIALIZER_VERSION,
    MaterializationState,
    NarrativeAnalysisAttachment,
    SignedNarrativeAuthorityBinding,
)
from app.corpus_steward.materialization_service import MaterializationService
from app.corpus_steward.narrative_extractor import NarrativeSourceExtractor
from app.corpus_steward.narrative_repository import SQLNarrativeAnalysisRepository
from app.corpus_steward.narrative_service import NarrativeSourceAnalysisService
from app.corpus_steward.registry import SQLAttestationRepository
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import SQLStructuredInputRepository
from app.corpus_steward.structured_repository import SQLStructuredPackageRepository
from app.persistence.models import MaterializationRunRow, MaterializedEvidenceRow
from tests.unit.test_narrative_anchored_closure import build_narrative_pipeline

ASSET = "NARRATIVE_GUIDELINE"


def _guideline() -> bytes:
    """A PDF whose pages carry more prose than one unit's budget.

    Sized deliberately: two blocks of roughly 700 characters exceed the 1,200-character
    unit budget together, so each page must split. A page that fits in one unit would let
    this suite pass without block grouping ever doing anything.
    """

    import pymupdf

    document = pymupdf.open()
    for index in range(4):
        page = document.new_page()
        page.insert_text((72, 20), f"{index + 1} Consolidated guideline", fontsize=9)
        page.insert_textbox(
            pymupdf.Rect(72, 60, 523, 380),
            "Adults with confirmed hypertension and blood pressure at or above "
            "140/90 mmHg should be started on pharmacological treatment. " * 6,
            fontsize=8,
        )
        page.insert_textbox(
            pymupdf.Rect(72, 400, 523, 700),
            f"Follow-up for cohort {index} should occur monthly until control "
            "is achieved, then at three-monthly intervals thereafter. " * 6,
            fontsize=8,
        )
    payload = document.tobytes()
    document.close()
    return payload


async def _pipeline(tmp_path: Path, pdf_bytes: bytes, *, narrative_extractor=None):
    database, trust_roots, reconciliation, inputs = await build_narrative_pipeline(
        tmp_path, pdf_bytes
    )
    reconciled = await reconciliation.reconcile(
        "NARRATIVE_TEST", idempotency_key="narrative-materialize"
    )
    candidate_id = reconciled.release_candidate.content.candidate_id
    await inputs.resolve(candidate_id)

    signer = Ed25519Signer.from_pem(
        tmp_path / "private.pem",
        key_id="narrative-test-key",
        signer_identity="narrative-test-steward",
    )
    artifacts = ImmutableStewardArtifactStore(tmp_path / "artifacts")
    narrative_repository = SQLNarrativeAnalysisRepository(database)
    census = NarrativeSourceAnalysisService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        repository=narrative_repository,
        ledger=SQLReconciliationLedger(database),
        attestations=SQLAttestationRepository(database),
        artifacts=artifacts,
        signer=signer,
    )
    materializer = MaterializationService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        repository=SQLMaterializationRepository(database),
        ledger=SQLReconciliationLedger(database),
        attestations=SQLAttestationRepository(database),
        artifacts=artifacts,
        extractor=DAKSourceExtractor(),
        signer=signer,
        narrative_repository=narrative_repository,
        narrative_extractor=narrative_extractor or NarrativeSourceExtractor(),
    )
    return database, census, materializer, candidate_id


@pytest.mark.asyncio
async def test_a_guideline_pdf_becomes_source_anchored_evidence(tmp_path) -> None:
    database, census, materializer, candidate_id = await _pipeline(tmp_path, _guideline())
    await census.analyse(candidate_id)

    result = await materializer.materialize_narrative(candidate_id)

    assert result.state is MaterializationState.READY_FOR_QA
    content = result.report.content
    assert content.materializer_name == NARRATIVE_MATERIALIZER_NAME
    assert content.materializer_version == NARRATIVE_MATERIALIZER_VERSION
    assert isinstance(content.authority_binding, SignedNarrativeAuthorityBinding)
    assert isinstance(content.structural_mapping, NarrativeAnalysisAttachment)
    assert content.coverage.complete is True
    assert content.blockers == ()
    assert len(content.evidence) > 4, "block grouping should out-produce one unit per page"
    assert result.corpus_release_candidate is not None

    async with database.session() as session:
        rows = await session.scalar(
            select(func.count()).select_from(MaterializedEvidenceRow)
        )
        run = await session.scalar(select(MaterializationRunRow))
    assert rows == len(content.evidence)
    assert run.narrative_run_id is not None
    assert run.structured_run_id is None, "a narrative run descends from a census, not a package"
    assert run.inventory_item_id == ASSET
    await database.close()


@pytest.mark.asyncio
async def test_evidence_anchors_to_block_groups_not_pages(tmp_path) -> None:
    database, census, materializer, candidate_id = await _pipeline(tmp_path, _guideline())
    await census.analyse(candidate_id)
    result = await materializer.materialize_narrative(candidate_id)

    unit_ids = [entry.source_unit_id for entry in result.report.content.evidence]
    assert all(":block:" in unit_id for unit_id in unit_ids)
    # The running header was dropped, so no unit starts at block 0 of a page.
    assert all(not unit_id.endswith(":block:0") for unit_id in unit_ids)
    await database.close()


@pytest.mark.asyncio
async def test_materialization_refuses_to_promote_when_the_census_disagrees(tmp_path) -> None:
    """The check the whole stage exists for.

    The census enumerated block groups; this materializer is handed a page extractor, so
    the recomputed inventory cannot match. Nothing is promoted, and the report says why.
    """

    database, census, materializer, candidate_id = await _pipeline(
        tmp_path, _guideline(), narrative_extractor=DAKSourceExtractor()
    )
    await census.analyse(candidate_id)

    result = await materializer.materialize_narrative(candidate_id)

    assert result.state is MaterializationState.BLOCKED
    assert f"UNIT_INVENTORY_MISMATCH:{ASSET}" in result.report.content.blockers
    assert result.report.content.ready_for_qa is False
    assert result.corpus_release_candidate is None
    assert result.report.content.evidence == ()
    await database.close()


@pytest.mark.asyncio
async def test_materialization_requires_a_census_that_cleared_its_own_gate(tmp_path) -> None:
    database, _census, materializer, candidate_id = await _pipeline(tmp_path, _guideline())

    with pytest.raises(ValueError, match="requires a narrative source analysis"):
        await materializer.materialize_narrative(candidate_id)
    await database.close()


@pytest.mark.asyncio
async def test_narrative_materialization_is_idempotent(tmp_path) -> None:
    database, census, materializer, candidate_id = await _pipeline(tmp_path, _guideline())
    await census.analyse(candidate_id)

    first = await materializer.materialize_narrative(candidate_id)
    second = await materializer.materialize_narrative(candidate_id)

    assert first.report.report_sha256 == second.report.report_sha256
    async with database.session() as session:
        runs = await session.scalar(select(func.count()).select_from(MaterializationRunRow))
    assert runs == 1
    await database.close()


@pytest.mark.asyncio
async def test_the_narrative_run_id_includes_the_item(tmp_path) -> None:
    """The defect the structured derivation cannot fix, not inherited here.

    `_run_id` hashes only the candidate, so a second item would collide with the first.
    The narrative derivation includes the item from its first run.
    """

    first = MaterializationService._narrative_run_id("RC_x", "item-one")
    second = MaterializationService._narrative_run_id("RC_x", "item-two")
    structured_first = MaterializationService._run_id("RC_x")

    assert first != second
    assert MaterializationService._run_id("RC_x") == structured_first
