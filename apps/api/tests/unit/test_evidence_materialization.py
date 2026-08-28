import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula
from pydantic import ValidationError

from app.corpus_steward.cli import materialization_schema_document
from app.corpus_steward.evidence_extractor import XLSX_MEDIA_TYPE, DAKSourceExtractor
from app.corpus_steward.materialization_service import MaterializationService
from app.corpus_steward.schemas import (
    STAGE_ORDER,
    AttestationPurpose,
    InventoryItem,
    ReconciliationCandidateContent,
    ReconciliationReleaseCandidate,
    ReconciliationSnapshot,
    ReconciliationStage,
    SourceArtifactReference,
    TrustRootDefinition,
    VerifiedAttestationReference,
)
from app.corpus_steward.structured_repository import StructuredSourceContext
from app.schemas.corpus import LocatorKind
from app.schemas.domain import SourceStatus

# Tracks MATERIALIZER_VERSION, so it moves whenever that does. The 1.0.0 document stays
# checked in beside it: it is the contract the already-signed release was built under, and
# nothing about bumping the version retracts it.
MATERIALIZATION_SCHEMA = (
    Path(__file__).resolve().parents[4]
    / "packages"
    / "schemas"
    / "corpus-materialization-result-1.1.0.schema.json"
)
FIXED_TIME = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
def test_xlsx_extraction_accounts_for_rows_and_cell_anchors() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Decision logic"
    worksheet.append(["Rule", "Action"])
    worksheet.append([])
    worksheet.append(["CD4 < 200", "Start prophylaxis"])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    extracted = DAKSourceExtractor().extract(
        output.getvalue(),
        media_type=XLSX_MEDIA_TYPE,
        source_uri="https://example.test/annex.xlsx",
    )

    assert extracted.expected_source_units == 3
    assert extracted.empty_source_units == 1
    assert len(extracted.units) == 2
    assert extracted.units[1].source_unit_id == "xlsx:Decision logic:row:3"
    assert extracted.units[1].content_exact == "A3=CD4 < 200\nB3=Start prophylaxis"
    assert all(anchor.kind is LocatorKind.TABLE_CELL for anchor in extracted.units[1].anchors)
    assert extracted.units[1].anchors[1].column_index == 1


def test_xlsx_array_formulas_have_stable_exact_content() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Formula"
    worksheet["A1"] = ArrayFormula("A1", "=SUM(B1:B2)")
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    extractor = DAKSourceExtractor()
    first = extractor.extract(
        output.getvalue(), media_type=XLSX_MEDIA_TYPE, source_uri="https://example.test/a.xlsx"
    )
    second = extractor.extract(
        output.getvalue(), media_type=XLSX_MEDIA_TYPE, source_uri="https://example.test/a.xlsx"
    )

    assert first == second
    assert first.units[0].content_exact == "A1==SUM(B1:B2)"
    assert "object at 0x" not in first.units[0].content_exact


def test_trust_root_rejects_combined_global_and_asset_licensing() -> None:
    common = {
        "trust_root_id": "LICENSE_TEST",
        "publisher_id": "PUB_TEST",
        "publisher_name": "Test",
        "allowed_domains": ["example.test"],
        "jurisdictions": ["WORLD"],
        "product_families": ["TEST"],
        "polling_interval_seconds": 3600,
        "connector_name": "synthetic",
        "connector_version": "1.0.0",
        "trusted_stage_key_ids": ["key"],
        "licensing_policy": {
            "license_id": "LEGACY",
            "policy_url": "https://example.test/legacy",
            "acquisition_allowed": True,
        },
        "asset_licensing": [
            {
                "asset_id": "ASSET",
                "asset_kind": "NARRATIVE_SOURCE",
                "license_id": "ASSET-LICENSE",
                "policy_url": "https://example.test/asset",
                "acquisition_allowed": True,
            }
        ],
    }

    with pytest.raises(ValidationError, match="cannot be combined"):
        TrustRootDefinition.model_validate(common)


def test_materialization_schema_is_checked_in() -> None:
    assert json.loads(MATERIALIZATION_SCHEMA.read_text(encoding="utf-8")) == (
        materialization_schema_document()
    )


# ---------------------------------------------------------------------------------------
# The structured materializer derives one run per candidate. On a multi-item candidate that
# derivation returns the first item's run for every item, silently. The derivation is
# committed to signed history and cannot move, so the guard makes the wrong answer loud.
# See docs/narrative-only-materialization.md, "Run identity: two materializers, no cascade".
# ---------------------------------------------------------------------------------------


def _artifact_reference(item_id: str, seed: str) -> SourceArtifactReference:
    return SourceArtifactReference(
        item_id=item_id,
        artifact_sha256=hashlib.sha256(seed.encode()).hexdigest(),
        byte_size=1024,
        media_type="application/pdf",
        requested_url=f"https://example.test/{item_id}",
        final_url=f"https://example.test/{item_id}",
        fetched_at=FIXED_TIME,
    )


def _stage_attestation(stage: ReconciliationStage) -> VerifiedAttestationReference:
    return VerifiedAttestationReference(
        attestation_id=f"ATT_{stage.value}",
        purpose=AttestationPurpose.STAGE,
        predicate_type=f"https://med-rag.local/attestations/reconciliation/{stage.value}",
        statement_sha256=hashlib.sha256(stage.value.encode()).hexdigest(),
        signature_sha256=hashlib.sha256(f"sig-{stage.value}".encode()).hexdigest(),
        signer_identity="test-steward",
        signing_key_id="test-key",
        verified_at=FIXED_TIME,
    )


def _candidate(item_ids: tuple[str, ...]) -> ReconciliationReleaseCandidate:
    return ReconciliationReleaseCandidate.seal(
        ReconciliationCandidateContent(
            candidate_id="RC_multi_item",
            job_id="JOB_multi_item",
            created_at=FIXED_TIME,
            trust_root_sha256=hashlib.sha256(b"trust-root").hexdigest(),
            snapshot=ReconciliationSnapshot(
                trust_root_id="NARRATIVE_TEST",
                publisher_id="WHO",
                cutoff_at=FIXED_TIME,
                inventory_artifact_sha256=hashlib.sha256(b"inventory").hexdigest(),
                expected_item_ids=item_ids,
                included_item_ids=item_ids,
                complete=True,
            ),
            changes=(),
            source_artifacts=tuple(
                _artifact_reference(item_id, item_id) for item_id in item_ids
            ),
            exceptions=(),
            stage_attestations=tuple(_stage_attestation(stage) for stage in STAGE_ORDER),
        )
    )


class _StubSourceRepository:
    """Returns one item's context out of a candidate that carries several."""

    def __init__(self, candidate: ReconciliationReleaseCandidate) -> None:
        self._candidate = candidate

    async def source_context(
        self, candidate_id: str, *, item_id: str | None = None
    ) -> StructuredSourceContext:
        source = next(
            item
            for item in self._candidate.content.source_artifacts
            if item_id is None or item.item_id == item_id
        )
        return StructuredSourceContext(
            candidate=self._candidate,
            inventory_item=InventoryItem(
                item_id=source.item_id,
                title=source.item_id,
                version="1",
                lifecycle_status=SourceStatus.EFFECTIVE,
                canonical_url=source.final_url,
                artifact_url=source.final_url,
                media_type=source.media_type,
            ),
            source_artifact=source,
            artifact_storage_key=f"sha256/{source.artifact_sha256}",
        )


def _materializer(candidate: ReconciliationReleaseCandidate) -> MaterializationService:
    # Only the source repository is reached: the guard raises before any other collaborator
    # is touched, which is the point -- nothing is written for a candidate it cannot address.
    return MaterializationService(
        trust_roots=None,  # type: ignore[arg-type]
        source_repository=_StubSourceRepository(candidate),  # type: ignore[arg-type]
        input_repository=None,  # type: ignore[arg-type]
        repository=None,  # type: ignore[arg-type]
        ledger=None,  # type: ignore[arg-type]
        attestations=None,  # type: ignore[arg-type]
        artifacts=None,  # type: ignore[arg-type]
        extractor=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
    )


async def test_structured_materialization_refuses_a_multi_item_candidate() -> None:
    candidate = _candidate(("ITEM_A", "ITEM_B"))
    materializer = _materializer(candidate)

    with pytest.raises(ValueError, match="one run per candidate"):
        await materializer.materialize("RC_multi_item", item_id="ITEM_B")


async def test_structured_materialization_still_accepts_a_single_item_candidate() -> None:
    """The guard is about run identity, not about rejecting work: one item still proceeds."""

    candidate = _candidate(("ITEM_A",))
    materializer = _materializer(candidate)

    # Past the guard, into the trust-root lookup, which the stub cannot serve.
    with pytest.raises(AttributeError):
        await materializer.materialize("RC_multi_item", item_id="ITEM_A")
