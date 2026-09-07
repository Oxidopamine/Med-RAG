"""The narrative source analysis stage, end to end against a real closure.

The census is only worth signing if materialization can recompute it and disagree, so the
tests that matter here are about what the *stored, signed* report binds - not about the
counting, which `test_narrative_analyzer.py` already covers. Reuses the closure harness
because a census with no resolved closure behind it has nothing to bind to.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.narrative_repository import SQLNarrativeAnalysisRepository
from app.corpus_steward.narrative_schemas import (
    NARRATIVE_PROCESSOR_NAME,
    NARRATIVE_PROCESSOR_VERSION,
    NarrativeCheckOutcome,
    NarrativeRunState,
)
from app.corpus_steward.narrative_service import (
    NARRATIVE_ATTESTATION_PREDICATE,
    NarrativeSourceAnalysisService,
)
from app.corpus_steward.registry import SQLAttestationRepository
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import SQLStructuredInputRepository
from app.corpus_steward.structured_repository import SQLStructuredPackageRepository
from app.persistence.models import NarrativeAnalysisRunRow, StewardArtifactRow
from tests.unit.test_narrative_anchored_closure import (
    _pdf,
    build_narrative_pipeline,
)


async def _analysed(tmp_path: Path, pdf_bytes: bytes):
    """Reconcile, resolve the closure, then census - the real stage order."""

    database, trust_roots, reconciliation, inputs = await build_narrative_pipeline(
        tmp_path, pdf_bytes
    )
    reconciled = await reconciliation.reconcile(
        "NARRATIVE_TEST", idempotency_key="narrative-census"
    )
    candidate_id = reconciled.release_candidate.content.candidate_id
    await inputs.resolve(candidate_id)

    # The same key the harness registered and the trust root trusts, rebuilt from the PEM
    # it wrote rather than reached out of the closure service.
    signer = Ed25519Signer.from_pem(
        tmp_path / "private.pem",
        key_id="narrative-test-key",
        signer_identity="narrative-test-steward",
    )
    service = NarrativeSourceAnalysisService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        repository=SQLNarrativeAnalysisRepository(database),
        ledger=SQLReconciliationLedger(database),
        attestations=SQLAttestationRepository(database),
        artifacts=ImmutableStewardArtifactStore(tmp_path / "artifacts"),
        signer=signer,
    )
    return database, service, candidate_id


@pytest.mark.asyncio
async def test_census_is_signed_stored_and_idempotent(tmp_path) -> None:
    database, service, candidate_id = await _analysed(tmp_path, _pdf())

    result = await service.analyse(candidate_id)
    repeated = await service.analyse(candidate_id)

    assert result.state is NarrativeRunState.VALIDATED
    assert repeated.report.report_sha256 == result.report.report_sha256
    assert repeated.attestation.attestation_id == result.attestation.attestation_id

    content = result.report.content
    assert content.processor_name == NARRATIVE_PROCESSOR_NAME
    assert content.processor_version == NARRATIVE_PROCESSOR_VERSION
    assert content.promotion_eligible is True
    assert content.blockers == ()
    assert content.unit_count_total == sum(item.unit_count for item in content.documents)

    async with database.session() as session:
        runs = await session.scalar(
            select(func.count()).select_from(NarrativeAnalysisRunRow)
        )
        report_artifacts = await session.scalar(
            select(func.count())
            .select_from(StewardArtifactRow)
            .where(StewardArtifactRow.kind == "NARRATIVE_ANALYSIS_REPORT")
        )
    assert runs == 1, "a repeated census must not write a second run"
    assert report_artifacts == 1
    await database.close()


@pytest.mark.asyncio
async def test_the_report_binds_the_closure_it_was_taken_behind(tmp_path) -> None:
    """Job 2 of the structured report: a second independent path back to the anchors."""

    database, service, candidate_id = await _analysed(tmp_path, _pdf())
    result = await service.analyse(candidate_id)
    content = result.report.content

    assert content.reconciliation_candidate_id == candidate_id
    assert content.structured_input_run_id is not None
    assert content.input_closure_sha256 is not None
    assert content.inventory_item_id == "NARRATIVE_GUIDELINE"
    assert content.documents[0].asset_id == content.inventory_item_id, (
        "asset_id == item_id is the narrative topology, not a shortcut"
    )
    assert content.documents[0].artifact_sha256 == content.source_artifact_sha256
    await database.close()


@pytest.mark.asyncio
async def test_every_declared_check_runs_before_promotion_is_offered(tmp_path) -> None:
    database, service, candidate_id = await _analysed(tmp_path, _pdf())
    result = await service.analyse(candidate_id)

    outcomes = {check.code.value: check.outcome for check in result.report.content.checks}
    assert set(outcomes) == {
        "DOCUMENT_SAFETY",
        "DOCUMENT_IDENTITY",
        "UNIT_COVERAGE",
        "LICENSE_POLICY",
        "NARRATIVE_AUTHORITY",
    }
    assert outcomes["DOCUMENT_SAFETY"] is NarrativeCheckOutcome.PASS
    assert outcomes["NARRATIVE_AUTHORITY"] is NarrativeCheckOutcome.PASS
    # The synthetic PDF carries no XMP rights statement. Silence is not a contradiction of
    # the operator's policy, so this warns and does not block.
    assert outcomes["LICENSE_POLICY"] is NarrativeCheckOutcome.WARN
    assert "NO_DECLARED_LICENSE:NARRATIVE_GUIDELINE" in result.report.content.warnings
    assert result.report.content.promotion_eligible is True
    await database.close()


@pytest.mark.asyncio
async def test_the_attestation_covers_the_report_under_its_own_predicate(tmp_path) -> None:
    database, service, candidate_id = await _analysed(tmp_path, _pdf())
    result = await service.analyse(candidate_id)

    assert result.attestation.predicate_type == NARRATIVE_ATTESTATION_PREDICATE
    assert result.report_artifact_sha256
    await database.close()


@pytest.mark.asyncio
async def test_a_document_with_no_addressable_units_is_blocked(tmp_path) -> None:
    """A census that enumerates nothing must not offer the asset for promotion.

    Materializing it would bind an asset carrying no evidence, and the release would be
    signed over an empty contribution.
    """

    import pymupdf

    document = pymupdf.open()
    document.new_page()
    blank = document.tobytes()
    document.close()

    database, service, candidate_id = await _analysed(tmp_path, blank)
    result = await service.analyse(candidate_id)

    assert result.state is NarrativeRunState.BLOCKED
    assert result.report.content.promotion_eligible is False
    assert "NO_ADDRESSABLE_UNITS:NARRATIVE_GUIDELINE" in result.report.content.blockers
    outcomes = {check.code.value: check.outcome for check in result.report.content.checks}
    assert outcomes["UNIT_COVERAGE"] is NarrativeCheckOutcome.BLOCK
    await database.close()


@pytest.mark.asyncio
async def test_a_stored_run_is_refused_if_its_columns_and_json_disagree(tmp_path) -> None:
    """The row and the report are two statements about one run.

    If they disagree, one was written by something that did not go through the repository,
    and neither can be trusted as the census materialization is checked against.
    """

    database, service, candidate_id = await _analysed(tmp_path, _pdf())
    await service.analyse(candidate_id)

    async with database.session() as session:
        row = await session.scalar(select(NarrativeAnalysisRunRow))
        row.unit_count_total = row.unit_count_total + 1
        await session.commit()

    repository = SQLNarrativeAnalysisRepository(database)
    with pytest.raises(Exception, match="inconsistent"):
        await repository.existing(
            candidate_id=candidate_id,
            item_id="NARRATIVE_GUIDELINE",
            processor_name=NARRATIVE_PROCESSOR_NAME,
            processor_version=NARRATIVE_PROCESSOR_VERSION,
        )
    await database.close()
