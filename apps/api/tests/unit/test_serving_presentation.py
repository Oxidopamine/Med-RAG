"""The presentation view and the two serving behaviours that depend on it.

Three defects surfaced on first contact with real questions, and all three are checked
here: coordinates in passage text, the WHO ``all`` worksheet consuming output slots with
copies, and a data-dictionary code entry satisfying the role gate that blocks unsafe
answers. Nothing here re-materializes evidence - every assertion is over a view.
"""

from __future__ import annotations

import pytest

from app.corpus_steward.qdrant_index import stable_qdrant_point_id
from app.reasoning.presentation import (
    PassageKind,
    PassagePresenter,
    discover_table_schemas,
    parse_spreadsheet_row,
)
from app.reasoning.retrieval_service import (
    SERVABLE_LIFECYCLE_STATES,
    ServingRetrievalService,
)
from app.schemas.corpus import (
    CorpusEvidenceRecord,
    EvidenceApprovalStatus,
    EvidenceRole,
    EvidenceVerification,
    EvidenceVerificationCheck,
    LocatorKind,
    SourceAnchor,
    VerificationInvariant,
    VerificationOutcome,
)
from app.schemas.domain import SourceStatus

RELEASE_ID = "CR_TEST"
ANNEX_A = "WHO_HIV_DAK_2:2:WHO_HIV_DAK_2_ANNEX_A"
ANNEX_B = "WHO_HIV_DAK_2:2:WHO_HIV_DAK_2_ANNEX_B"
DIGEST = "a" * 64

# The real shape of an Annex A row: one activity, one data element, an entry-type column
# saying this is a code in a value set, and a terminology crosswalk. Nothing in it
# recommends anything, and every one of them is labelled PRIMARY_SUPPORT by the corpus.
DTG_ROW = "\n".join(
    (
        "A145=HIV.D8 Capture or update client history",
        "B145=HIV.D.DE144",
        "C145=DTG",
        "D145=Treated with dolutegravir (DTG)",
        "E145=Input Option",
        "F145=Codes",
        "G145=DTG",
        "H145=N/A",
        "J145=None",
        "K145=O",
        "M145=HIV.D21.2.DT",
        "P145=XM7EE8",
        "W145=Not classifiable in LOINC",
        "AH145=703121000",
    )
)
# The same row as the ``all`` worksheet carries it: identical content at a different
# sheet offset, plus one column naming the tab it was copied from.
DTG_ROW_IN_ALL_SHEET = "\n".join(
    (
        "A1094=HIV.D8 Capture or update client history",
        "B1094=HIV.D.DE144",
        "C1094=DTG",
        "D1094=Treated with dolutegravir (DTG)",
        "E1094=Input Option",
        "F1094=Codes",
        "G1094=DTG",
        "H1094=N/A",
        "J1094=None",
        "K1094=O",
        "M1094=HIV.D21.2.DT",
        "P1094=XM7EE8",
        "W1094=Not classifiable in LOINC",
        "AH1094=703121000",
        "AK1094=HIV.D",
    )
)
DECISION_HEADER_ROW = "\n".join(
    (
        "B7=R",
        "C7=ART regimen composition",
        "D7=Medication/drug",
        "E7=Age",
        "F7=Output Type",
        "G7=Action",
        "H7=Guidance",
        "I7=Reference(s)",
    )
)
DECISION_RULE_ROW = "\n".join(
    (
        "B8=HIV.D21.2.DT.01",
        'C8="ART regimen composition" IN \'DTG\'',
        'D8="Medication/drug"=\'Rifampicin\'',
        'E8="Age" ≥ 10 years',
        "F8=PlanDefinition",
        'G8=Set "Dose adjustment recommended"=True',
        "H8=Double the daily dose of dolutegravir.",
        "I8=Consolidated guidelines, 2021, Table 4.14",
    )
)


def evidence(
    evidence_id: str,
    content_exact: str,
    *,
    source_version_id: str = ANNEX_A,
    table_id: str | None = "HIV.D Care-Treatment",
    row_index: int = 144,
    roles: tuple[EvidenceRole, ...] = (EvidenceRole.PRIMARY_SUPPORT,),
) -> CorpusEvidenceRecord:
    if table_id is None:
        anchors = (
            SourceAnchor(
                kind=LocatorKind.PDF,
                source_uri="https://example.invalid/dak.pdf",
                pdf_page=42,
            ),
        )
    else:
        anchors = tuple(
            SourceAnchor(
                kind=LocatorKind.TABLE_CELL,
                source_uri="https://example.invalid/annex.xlsx",
                table_id=table_id,
                row_index=row_index,
                column_index=index,
            )
            for index in range(2)
        )
    return CorpusEvidenceRecord(
        corpus_release_id=RELEASE_ID,
        evidence_id=evidence_id,
        source_id="SRC_TEST",
        source_version_id=source_version_id,
        source_artifact_sha256=DIGEST,
        publisher_id="WHO",
        jurisdiction="GLOBAL",
        language="en",
        lifecycle_status=SourceStatus.EFFECTIVE,
        evidence_roles=roles,
        content_exact=content_exact,
        content_search=" ".join(content_exact.split()),
        anchors=anchors,
        render_allowed=False,
        verification=EvidenceVerification(
            approval_status=EvidenceApprovalStatus.APPROVED,
            checks=tuple(
                EvidenceVerificationCheck(
                    invariant=invariant,
                    outcome=VerificationOutcome.PASS,
                    verifier="test@1.0.0",
                    evidence_digest=DIGEST,
                )
                for invariant in (
                    VerificationInvariant.CRITICAL_FIELDS,
                    VerificationInvariant.EXACT_CONTENT,
                    VerificationInvariant.PROVENANCE,
                )
            ),
        ),
    )


def test_parse_keeps_multi_line_cells_and_rejects_narrative() -> None:
    cells = parse_spreadsheet_row('B8=Set "x"=True\n\nThen stop.\nC8=Guidance')
    assert cells is not None
    assert [cell.column for cell in cells] == ["B", "C"]
    assert cells[0].value == 'Set "x"=True\n\nThen stop.'
    # Page text is not cell-addressed, and guessing at it would be worse than passing it
    # through.
    assert parse_spreadsheet_row("4\nDigital adaptation kit for HIV") is None


def test_parse_does_not_split_a_cell_on_text_that_looks_addressed() -> None:
    # Free text inside a guidance cell can begin a line with something the cell pattern
    # matches. It addresses neither this row nor a later column, so it stays content.
    cells = parse_spreadsheet_row("G8=Switch when\nCD4=250 cells/mm3\nH8=Guidance")

    assert cells is not None
    assert [cell.column for cell in cells] == ["G", "H"]
    assert cells[0].value == "Switch when\nCD4=250 cells/mm3"


def test_data_dictionary_row_renders_as_labelled_facts() -> None:
    rendered = PassagePresenter().render(evidence("EV_1", DTG_ROW))

    assert rendered.kind is PassageKind.DATA_DICTIONARY_ENTRY
    assert "A145=" not in rendered.text
    assert rendered.title == "HIV.D Care-Treatment - data element HIV.D.DE144 DTG"
    assert "- Description and Definition: Treated with dolutegravir (DTG)" in rendered.body
    assert "- Multiple Choice Type (if applicable): Input Option" in rendered.body
    # Placeholder cells carry no information for a reader.
    assert "N/A" not in rendered.body
    assert "Not classifiable" not in rendered.body
    # Six terminology columns would otherwise be six lines of codes.
    assert "- Terminology: ICD-11 XM7EE8; SNOMED GPS 703121000" in rendered.body


def test_all_worksheet_copy_shares_the_original_fingerprint() -> None:
    presenter = PassagePresenter()
    original = presenter.render(evidence("EV_1", DTG_ROW))
    copy = presenter.render(
        evidence("EV_2", DTG_ROW_IN_ALL_SHEET, table_id="all", row_index=1093)
    )
    different = presenter.render(
        evidence("EV_3", DTG_ROW.replace("C145=DTG", "C145=ABC"))
    )

    assert copy.fingerprint == original.fingerprint
    assert different.fingerprint != original.fingerprint


def test_decision_table_labels_come_from_the_release_header_row() -> None:
    records = [
        evidence(
            "EV_HEADER",
            DECISION_HEADER_ROW,
            source_version_id=ANNEX_B,
            table_id="HIV.D21.2.DT Drug Interactions",
            row_index=6,
        ),
        evidence(
            "EV_RULE",
            DECISION_RULE_ROW,
            source_version_id=ANNEX_B,
            table_id="HIV.D21.2.DT Drug Interactions",
            row_index=7,
        ),
    ]
    presenter = PassagePresenter.for_release(records)

    header = presenter.render(records[0])
    rule = presenter.render(records[1])

    # The header names the columns; serving it as clinical evidence is meaningless.
    assert header.kind is PassageKind.TABLE_HEADER
    assert not header.is_recommendation_bearing

    assert rule.kind is PassageKind.DECISION_RULE
    assert rule.is_recommendation_bearing
    assert rule.title.endswith("decision rule HIV.D21.2.DT.01")
    body = rule.body.splitlines()
    assert body.index("When:") < body.index("Then:")
    assert '- Medication/drug: "Medication/drug"=\'Rifampicin\'' in body
    assert body.index("- Guidance: Double the daily dose of dolutegravir.") > body.index(
        "Then:"
    )


def test_ambiguous_decision_header_is_skipped_rather_than_resolved() -> None:
    duplicated = [
        evidence(
            "EV_HEADER_1",
            DECISION_HEADER_ROW,
            source_version_id=ANNEX_B,
            table_id="HIV.D21.2.DT Drug Interactions",
            row_index=6,
        ),
        evidence(
            "EV_HEADER_2",
            DECISION_HEADER_ROW.replace("C7=ART regimen composition", "C7=Output Type"),
            source_version_id=ANNEX_B,
            table_id="HIV.D21.2.DT Drug Interactions",
            row_index=20,
        ),
    ]

    assert discover_table_schemas(duplicated) == {}


@pytest.mark.parametrize(
    ("content", "table_id", "expected", "bearing"),
    [
        (
            "B9=Preferred first-line ART regimens for adults",
            "HIV.D21.1.DT ART Regimen",
            PassageKind.TABLE_SECTION_TITLE,
            False,
        ),
        (
            "B2=Decision ID\nC2=HIV.D17.DT",
            "HIV.D17.DT Treatment Failure",
            PassageKind.TABLE_METADATA,
            False,
        ),
        (
            "B1=Note: this tab is drawn from Table 4.11 of the consolidated guidelines.\n"
            "\nSource: Consolidated guidelines on HIV, 2021",
            "HIV.D17.DT Treatment Failure",
            PassageKind.TABLE_NOTE,
            True,
        ),
        (
            "B4=condition\nC4=action",
            "Some Future Publisher Sheet",
            PassageKind.UNLABELLED_TABLE_ROW,
            False,
        ),
    ],
)
def test_row_forms_are_classified_from_structure(
    content: str, table_id: str, expected: PassageKind, bearing: bool
) -> None:
    rendered = PassagePresenter().render(
        evidence("EV_X", content, source_version_id=ANNEX_B, table_id=table_id)
    )

    assert rendered.kind is expected
    assert rendered.is_recommendation_bearing is bearing
    # Even with no schema to label the columns, the coordinates are gone.
    assert "B4=" not in rendered.text


def test_narrative_passes_through_unlabelled() -> None:
    rendered = PassagePresenter().render(
        evidence("EV_PDF", "37\n\n\n\nStart ART   in all adults.", table_id=None)
    )

    assert rendered.kind is PassageKind.NARRATIVE
    assert rendered.is_recommendation_bearing
    assert rendered.text == "37\n\nStart ART in all adults."


class _Sparse:
    indices = [1]
    values = [1.0]


class _Embedded:
    dense = [0.0]
    sparse = _Sparse()


class _EmbeddingBackend:
    async def embed_queries(self, texts):
        return [_Embedded() for _ in texts]

    async def embed_sparse_queries(self, texts):
        return [_Sparse() for _ in texts]


class _Qdrant:
    """Returns one fixed ranking per lane, with payloads that match the records."""

    def __init__(self, records: dict[str, CorpusEvidenceRecord], order: list[str]) -> None:
        self._records = records
        self._order = order

    async def query_points(self, collection, request):
        return [
            {
                "id": stable_qdrant_point_id(evidence_id),
                "payload": {
                    "evidence_id": evidence_id,
                    "corpus_release_id": record.corpus_release_id,
                    "approval_status": "APPROVED",
                    "evidence_sha256": record.sha256,
                    "jurisdiction": record.jurisdiction,
                    "language": record.language,
                    "publisher_id": record.publisher_id,
                    "source_version_id": record.source_version_id,
                    "lifecycle_status": record.lifecycle_status.value,
                },
            }
            for evidence_id, record in ((key, self._records[key]) for key in self._order)
        ]


async def retrieve(records: list[CorpusEvidenceRecord], *, top_k: int):
    evidence_by_id = {record.evidence_id: record for record in records}
    service = ServingRetrievalService(
        _Qdrant(evidence_by_id, [record.evidence_id for record in records]),
        _EmbeddingBackend(),
        top_k=top_k,
    )
    return await service.retrieve(
        "when should ART be started?",
        collection="c",
        corpus_release_id=RELEASE_ID,
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        evidence=evidence_by_id,
    )


async def test_duplicate_does_not_consume_an_output_slot() -> None:
    records = [
        evidence("EV_ORIGINAL", DTG_ROW),
        evidence("EV_COPY", DTG_ROW_IN_ALL_SHEET, table_id="all", row_index=1093),
        evidence(
            "EV_OTHER",
            DTG_ROW.replace("C145=DTG", "C145=ABC"),
            row_index=200,
        ),
    ]

    result = await retrieve(records, top_k=2)

    # Without suppression the copy takes the second slot and EV_OTHER never appears.
    assert [passage.evidence_id for passage in result.passages] == [
        "EV_ORIGINAL",
        "EV_OTHER",
    ]
    assert result.suppressed_duplicate_count == 1
    # The suppressed record is attributed, not silently dropped.
    assert result.passages[0].duplicates_suppressed == ("EV_COPY",)


async def test_role_gate_is_not_satisfied_by_a_data_dictionary_entry() -> None:
    records = [
        evidence(
            "EV_CODE",
            DTG_ROW,
            roles=(EvidenceRole.PRIMARY_SUPPORT, EvidenceRole.APPLICABILITY),
        ),
    ]

    result = await retrieve(records, top_k=5)

    assert result.missing_required_roles == (EvidenceRole.PRIMARY_SUPPORT,)
    assert not result.is_answerable
    assert result.disqualified_role_claims == (
        ("EV_CODE", EvidenceRole.PRIMARY_SUPPORT),
    )
    # Only the role whose meaning is "this states the recommendation" is qualified.
    assert result.passages[0].qualified_roles == (EvidenceRole.APPLICABILITY,)


async def test_role_gate_passes_when_a_decision_rule_supplies_the_support() -> None:
    records = [
        evidence(
            "EV_CODE",
            DTG_ROW,
            roles=(EvidenceRole.PRIMARY_SUPPORT, EvidenceRole.APPLICABILITY),
        ),
        evidence(
            "EV_HEADER",
            DECISION_HEADER_ROW,
            source_version_id=ANNEX_B,
            table_id="HIV.D21.2.DT Drug Interactions",
            row_index=6,
        ),
        evidence(
            "EV_RULE",
            DECISION_RULE_ROW,
            source_version_id=ANNEX_B,
            table_id="HIV.D21.2.DT Drug Interactions",
            row_index=7,
        ),
    ]

    result = await retrieve(records, top_k=5)

    assert result.missing_required_roles == ()
    assert result.is_answerable
    # The header row is retrieved and rendered, and still cannot supply the support.
    header = next(p for p in result.passages if p.evidence_id == "EV_HEADER")
    assert header.qualified_roles == ()


async def test_generation_sees_the_view_and_citation_stays_on_the_record() -> None:
    records = [evidence("EV_ORIGINAL", DTG_ROW)]

    result = await retrieve(records, top_k=5)
    passage = result.passages[0]

    assert "A145=" not in passage.rendered_text
    # The immutable content is carried unchanged alongside the view.
    assert passage.content_exact == DTG_ROW


def test_release_filter_admits_only_servable_lifecycle_states() -> None:
    """Lifecycle is a filter, not a ranking signal.

    A superseded edition can be near-identical in text to the current one, so neither
    fusion nor duplicate suppression can be trusted to prefer the right member. The
    allowlist is asserted by value so that adding a `SourceStatus` member does not
    silently make it servable.
    """

    service = ServingRetrievalService(qdrant=None, embedding_backend=None)
    query_filter = service._release_filter(
        "CR_test", jurisdictions=(), languages=()
    )
    clauses = {
        clause["key"]: clause["match"]
        for clause in query_filter["must"]
        if "key" in clause
    }

    assert clauses["lifecycle_status"] == {"any": ["EFFECTIVE"]}
    assert SourceStatus.SUPERSEDED not in SERVABLE_LIFECYCLE_STATES
    assert SourceStatus.WITHDRAWN not in SERVABLE_LIFECYCLE_STATES
    assert SourceStatus.PARTIALLY_SUPERSEDED not in SERVABLE_LIFECYCLE_STATES
