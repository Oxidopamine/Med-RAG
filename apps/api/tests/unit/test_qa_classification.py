from datetime import datetime, timezone

from app.corpus_steward.cli import build_parser
from app.corpus_steward.materialization_schemas import (
    MaterializedEvidenceContent,
    MaterializedEvidenceRecord,
)
from app.corpus_steward.qa_classification import (
    automated_decision_batch,
)
from app.corpus_steward.qa_schemas import (
    QAClassificationInput,
    QAClassificationInputItem,
    QADisposition,
    QAQuarantineReason,
)
from app.schemas.corpus import EvidenceRole, LocatorKind, SourceAnchor
from app.schemas.domain import SourceStatus

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def evidence_record(
    evidence_id: str,
    content_search: str,
    *,
    source_unit_id: str = "xlsx:HIV.D4.DT Screen for TB:row:8",
) -> MaterializedEvidenceRecord:
    return MaterializedEvidenceRecord.seal(
        MaterializedEvidenceContent(
            materialization_run_id="MAT_test",
            evidence_id=evidence_id,
            authority_binding_sha256="a" * 64,
            asset_id="WHO_HIV_DAK_2_ANNEX_B",
            source_artifact_sha256="b" * 64,
            source_unit_id=source_unit_id,
            source_title="WHO HIV DAK test source",
            source_version_id="WHO_HIV_DAK_2:test",
            publisher_id="PUB_WHO",
            jurisdiction="WORLD",
            language="en",
            lifecycle_status=SourceStatus.EFFECTIVE,
            content_exact=f"A1={content_search}",
            content_search=content_search,
            anchors=(
                SourceAnchor(
                    kind=LocatorKind.TABLE_CELL,
                    source_uri="https://iris.who.int/test.xlsx",
                    table_id="HIV.D4.DT Screen for TB",
                    row_index=7,
                    column_index=0,
                ),
            ),
            render_allowed=False,
        )
    )


def automated_batch_fixture():
    records = (
        evidence_record(
            "EV_failed",
            "<openpyxl.worksheet.formula.ArrayFormula object at 0x1234>",
            source_unit_id="xlsx:SEARCH:row:74",
        ),
        evidence_record(
            "EV_header",
            "Hit Policy Indicator Rule order",
            source_unit_id="xlsx:HIV.D4.DT Screen for TB:row:6",
        ),
        evidence_record(
            "EV_primary",
            "For HIV-positive adults with CD4 < 200 cells/mm start prophylaxis; "
            "strong recommendation based on high-certainty evidence.",
        ),
        evidence_record(
            "EV_duplicate",
            "For HIV-positive adults with CD4 < 200 cells/mm start prophylaxis; "
            "strong recommendation based on high-certainty evidence.",
            source_unit_id="xlsx:HIV.D4.DT Screen for TB:row:9",
        ),
    )
    items = tuple(
        QAClassificationInputItem(
            evidence_id=record.content.evidence_id,
            materialized_evidence_sha256=record.evidence_sha256,
            asset_id=record.content.asset_id,
            source_unit_id=record.content.source_unit_id,
            replay_passed=record.content.evidence_id != "EV_failed",
        )
        for record in records
    )
    classification_input = QAClassificationInput(
        qa_run_id="QA_test",
        corpus_release_candidate_id="CRC_test",
        evidence_artifact_manifest_sha256="c" * 64,
        evidence_count=len(records),
        replay_passed_count=3,
        replay_failed_count=1,
        items=items,
    )
    return automated_decision_batch(
        classification_input,
        {record.content.evidence_id: record for record in records},
        materialization_candidate_id="CRC_test",
        decided_at=NOW,
    )


def test_classification_is_fail_closed_and_deduplicates() -> None:
    batch = automated_batch_fixture()
    records = {item.evidence_id: item for item in batch.decisions}

    failed = records["EV_failed"]
    assert failed.disposition is QADisposition.QUARANTINE
    assert set(failed.quarantine_reasons) == {
        QAQuarantineReason.ANCHOR_REPLAY_FAILED,
        QAQuarantineReason.EXTRACTION_STRUCTURE_FAILED,
    }
    assert records["EV_header"].quarantine_reasons == (
        QAQuarantineReason.HEADER_OR_FOOTER,
    )
    approved = [
        item for item in batch.decisions if item.disposition is QADisposition.APPROVE
    ]
    assert len(approved) == 1
    assert {
        EvidenceRole.PRIMARY_SUPPORT,
        EvidenceRole.APPLICABILITY,
        EvidenceRole.DOSE_OR_THRESHOLD,
    }.issubset(approved[0].evidence_roles)
    assert approved[0].recommendation_grade is not None
    assert records["EV_duplicate"].quarantine_reasons == (
        QAQuarantineReason.DUPLICATE,
    )


def test_automated_batch_covers_every_record_without_operator_input() -> None:
    batch = automated_batch_fixture()

    assert batch.corpus_release_candidate_id == "CRC_test"
    assert batch.decision_authority == "phase4-deterministic-classifier@1.0.0"
    assert batch.decided_at == NOW
    assert len(batch.decisions) == 4
    assert sum(item.disposition is QADisposition.APPROVE for item in batch.decisions) == 1


def test_cli_qa_has_no_manual_decision_flags_or_review_commands() -> None:
    parser = build_parser()
    qa = parser.parse_args(["qa", "CRC_123456"])

    assert qa.command == "qa"
    assert not hasattr(qa, "decisions")
    assert not hasattr(qa, "auto_approve")
    commands = parser._subparsers._group_actions[0].choices
    assert "qa-preclassify" not in commands
    assert "qa-finalize-review" not in commands
