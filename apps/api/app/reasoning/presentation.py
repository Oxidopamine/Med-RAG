"""Presentation views over immutable corpus evidence.

Nothing here changes what an evidence record *is*. ``content_exact``, the anchors, and
the evidence digest are what the QA ledger decided on and what the signed release binds,
so re-materializing a record to make it readable would invalidate both. This module is a
*view*: it reads an approved record and produces a rendering for a human reader and for
the generation lane, while citation stays bound to ``evidence_id`` and therefore to the
exact approved bytes.

Why a view is needed at all: XLSX evidence is anchored per cell, so ``content_exact``
for a spreadsheet row is the cell-addressed form ``A145=HIV.D8 ...`` the extractor emits.
That is the correct anchor - replaying it against the workbook is exactly what the QA
gate does - and it is unusable as passage text. A reader cannot read it, and a model
handed it spends its attention on column letters.

Three things fall out of parsing that form back into cells. All three are presentation
determinations over existing content, not new facts about it:

* a rendering that uses the publisher's own column labels instead of coordinates;
* a fingerprint over the substantive cells, which is what collapses the WHO ``all``
  worksheet - a verbatim copy of the thirteen topic worksheets carrying one extra column
  that names the tab a row was copied from - back onto the row it copies;
* a *form* classification. A data-dictionary entry states what a system records; it is
  not a clinical recommendation, whatever evidence role it was labelled with. The
  serving role gate consumes this; the reasoning for that lives in ``retrieval_service``.

Column labels come from the release itself wherever the publisher's header row survived
QA as an approved record, and from a declared schema where it did not. An unrecognised
table degrades to an unlabelled rendering rather than failing - a view that cannot label
a row can still strip its coordinates.

This does not touch ``render_allowed``. That flag gates display of the *source document*
- evidence cards and exact PDF highlighting - and is still unresolved for every WHO
asset. Rendering here re-arranges bytes the serving path already puts in front of a
reader and a model; it does not widen what may be shown.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from app.schemas.corpus import CorpusEvidenceRecord, LocatorKind

# ``A12=value``. A cell value may itself contain newlines - WHO decision tables put
# multi-line guidance in one cell - so a line that does not open a new cell continues the
# previous one. Anything before the first cell means this is not a cell-addressed row at
# all, and the caller passes the text through instead.
_CELL_LINE = re.compile(r"^([A-Z]{1,3})(\d+)=(.*)$")
_HORIZONTAL_SPACE = re.compile(r"[ \t]+")
_BLANK_RUN = re.compile(r"\n{3,}")

# Values the WHO workbooks use to mean "nothing here". Dropped from the rendering and
# kept in the fingerprint: an absent condition is not evidence to show a reader, but two
# rows differing in which conditions are absent are different rows.
_PLACEHOLDER_VALUES = frozenset(
    {"", "-", "--", "–", "n/a", "na", "none", "not applicable", "nil"}
)
_PLACEHOLDER_PREFIXES = ("not classifiable in",)


class PassageKind(str, Enum):
    """What form a passage takes, read from its structure and never from its role label."""

    NARRATIVE = "NARRATIVE"
    TABLE_NOTE = "TABLE_NOTE"
    DECISION_RULE = "DECISION_RULE"
    SCHEDULE_ENTRY = "SCHEDULE_ENTRY"
    INDICATOR_DEFINITION = "INDICATOR_DEFINITION"
    DATA_DICTIONARY_ENTRY = "DATA_DICTIONARY_ENTRY"
    TABLE_HEADER = "TABLE_HEADER"
    TABLE_SECTION_TITLE = "TABLE_SECTION_TITLE"
    TABLE_METADATA = "TABLE_METADATA"
    UNLABELLED_TABLE_ROW = "UNLABELLED_TABLE_ROW"


# Forms that can carry a clinical recommendation. This is a claim about *form*, not about
# any row's content: a data-dictionary entry defines a field to record, a header names
# columns, a section title names a block of rows, and none of the three can recommend
# anything. An unrecognised table is excluded because the view cannot tell what it is,
# and the gate that consumes this exists to fail closed.
RECOMMENDATION_BEARING_KINDS: frozenset[PassageKind] = frozenset(
    {
        PassageKind.NARRATIVE,
        PassageKind.TABLE_NOTE,
        PassageKind.DECISION_RULE,
        PassageKind.SCHEDULE_ENTRY,
        PassageKind.INDICATOR_DEFINITION,
    }
)

_KIND_TITLES = {
    PassageKind.DECISION_RULE: "decision rule",
    PassageKind.SCHEDULE_ENTRY: "service schedule",
    PassageKind.INDICATOR_DEFINITION: "indicator",
    PassageKind.DATA_DICTIONARY_ENTRY: "data element",
    PassageKind.TABLE_HEADER: "table columns",
    PassageKind.TABLE_SECTION_TITLE: "section",
    PassageKind.TABLE_METADATA: "table metadata",
    PassageKind.TABLE_NOTE: "note",
    PassageKind.UNLABELLED_TABLE_ROW: "row",
}


@dataclass(frozen=True)
class TableCell:
    column: str
    row: int
    value: str

    @property
    def ordinal(self) -> int:
        return column_ordinal(self.column)


@dataclass(frozen=True)
class CollapsedGroup:
    """Columns rendered on one line, so a terminology crosswalk is not six lines."""

    label: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class TableSchema:
    """How one worksheet's rows are read back into labelled facts."""

    schema_id: str
    row_kind: PassageKind
    labels: Mapping[str, str]
    # Columns that say where a row sits rather than what it says. Excluded from the
    # duplicate fingerprint, because two rows differing only here are the same row.
    structural_columns: frozenset[str] = frozenset()
    # Columns shown, in this order. Empty means every labelled column in sheet order.
    display_columns: tuple[str, ...] = ()
    collapsed: tuple[CollapsedGroup, ...] = ()
    # Columns naming the row. They title it instead of repeating in the body.
    identity_columns: tuple[str, ...] = ()
    # Where a decision table stops stating conditions and starts stating what to do.
    # Discovered from the publisher's own header row, never guessed.
    outcome_column: str | None = None
    # The worksheet row the labels were read from, when they were read from the release
    # rather than declared. That row is itself an approved record and will be retrieved,
    # so knowing which one it is keeps a list of column names from being served as
    # clinical evidence.
    header_row: int | None = None

    def label_for(self, column: str) -> str | None:
        return self.labels.get(column)


@dataclass(frozen=True)
class RenderedPassage:
    """One record as a reader and the generation lane see it."""

    evidence_id: str
    kind: PassageKind
    title: str
    body: str
    fingerprint: str
    schema_id: str | None = None

    @property
    def text(self) -> str:
        if not self.title:
            return self.body
        return f"{self.title}\n{self.body}".strip()

    @property
    def is_recommendation_bearing(self) -> bool:
        return self.kind in RECOMMENDATION_BEARING_KINDS


def column_ordinal(column: str) -> int:
    """``A`` -> 1, ``Z`` -> 26, ``AA`` -> 27. Sheet order, not lexicographic order."""

    ordinal = 0
    for character in column:
        ordinal = ordinal * 26 + (ord(character) - ord("A") + 1)
    return ordinal


def parse_spreadsheet_row(content_exact: str) -> tuple[TableCell, ...] | None:
    """Read the extractor's cell-addressed form back into cells.

    Returns ``None`` for anything that is not that form - PDF page text most obviously -
    so callers pass such content through untouched rather than guessing at it.
    """

    parsed: list[list[object]] = []
    row: int | None = None
    last_ordinal = 0
    for line in content_exact.split("\n"):
        match = _CELL_LINE.match(line)
        # A line only opens a new cell if it addresses the same row and a later column
        # than the one before it - which is the order the extractor writes them in. That
        # matters because guidance cells contain free text, and a line inside one that
        # happens to read like `CD4=250` must stay part of its cell rather than silently
        # becoming a new one.
        opens_cell = (
            match is not None
            and (row is None or int(match.group(2)) == row)
            and column_ordinal(match.group(1)) > last_ordinal
        )
        if opens_cell:
            assert match is not None
            row = int(match.group(2))
            last_ordinal = column_ordinal(match.group(1))
            parsed.append([match.group(1), row, match.group(3)])
        elif parsed:
            parsed[-1][2] = f"{parsed[-1][2]}\n{line}"
        else:
            return None
    if not parsed:
        return None
    return tuple(
        TableCell(column=str(cell[0]), row=int(cell[1]), value=str(cell[2]))
        for cell in parsed
    )


def is_placeholder(value: str) -> bool:
    lowered = value.strip().casefold()
    return lowered in _PLACEHOLDER_VALUES or lowered.startswith(_PLACEHOLDER_PREFIXES)


def table_anchor(record: CorpusEvidenceRecord) -> tuple[str, int] | None:
    """The worksheet and row a record was extracted from, if it is tabular."""

    for anchor in record.anchors:
        if anchor.kind is LocatorKind.TABLE_CELL and anchor.table_id is not None:
            return anchor.table_id, anchor.row_index or 0
    return None


def _normalize(value: str) -> str:
    collapsed = _HORIZONTAL_SPACE.sub(" ", value.replace("\r\n", "\n").replace("\r", "\n"))
    return _BLANK_RUN.sub("\n\n", "\n".join(line.strip() for line in collapsed.split("\n"))).strip()


def _fingerprint(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


# Caption rows: two cells, the first naming what the second is. The WHO decision-support
# workbook writes a table's identity this way, and the QA classifier already recognises
# the same keys as non-evidence when they appear at the head of a sheet.
_TABLE_CAPTION_KEYS = frozenset(
    {"decision id", "business rule", "trigger", "hit policy indicator", "schedule id"}
)
# A lone cell is a section heading when it reads like one. Longer or multi-line single
# cells are the workbook's own prose - the "Note: this tab has three decision support
# tables ..." blocks that cite the guideline a table was built from - and those are
# content.
_SECTION_TITLE_MAX_CHARS = 120

# The column that separates a decision table's conditions from what it says to do. It is
# the publisher's own label, and finding it in an approved row is how a decision table's
# header is located without hard-coding seventeen worksheet layouts.
_DECISION_OUTCOME_LABEL = "Output Type"


# --------------------------------------------------------------------------------------
# Declared worksheet schemas.
#
# These cover the worksheets whose header row did not survive QA: the Annex A data
# dictionary and the Annex C indicator sheets have headers matching
# ``qa_classification._HEADER_PREFIXES`` and were quarantined as HEADER_OR_FOOTER, and the
# Annex B schedule header was quarantined the same way. Labels below are transcribed from
# those quarantined header rows in the materialization artifact for
# ``WHO_HIV_DAK_2:2`` - they are the publisher's wording, not an editorial summary.
#
# Annex B decision tables are absent here on purpose. Their header rows *were* approved,
# so they are discovered from the release itself by ``discover_table_schemas``, which is
# both less to maintain and self-checking against the release being served.
# --------------------------------------------------------------------------------------

_ANNEX_A = "WHO_HIV_DAK_2:2:WHO_HIV_DAK_2_ANNEX_A"
_ANNEX_B = "WHO_HIV_DAK_2:2:WHO_HIV_DAK_2_ANNEX_B"
_ANNEX_C = "WHO_HIV_DAK_2:2:WHO_HIV_DAK_2_ANNEX_C"

_DATA_DICTIONARY_LABELS = {
    "A": "Activity",
    "B": "Data Element ID",
    "C": "Data Element Label",
    "D": "Description and Definition",
    "E": "Multiple Choice Type (if applicable)",
    "F": "Data Type",
    "G": "Input Options",
    "H": "Quantity Sub-type",
    "I": "Calculation",
    "J": "Validation Condition",
    "K": "Required",
    "L": "Explain Conditionality",
    "M": "Linkages to Decision Support Tables",
    "N": "Linkages to Aggregate Indicators",
    "O": "Annotations",
    "P": "ICD-11 Code",
    "Q": "ICD-11 URI",
    "R": "ICD-11 Comments / Considerations",
    "S": "ICD-11 Relationship",
    "T": "ICD-10 Code",
    "U": "ICD-10 Comments / Considerations",
    "V": "ICD-10 Relationship",
    "W": "LOINC version 2.74 Code",
    "X": "LOINC version 2.74 Comments / Considerations",
    "Y": "LOINC version 2.74 Relationship",
    "Z": "ICHI (Beta 3) Code",
    "AA": "ICHI URI",
    "AB": "ICHI Comments / Considerations",
    "AC": "ICHI Relationship",
    "AD": "ICF Code",
    "AE": "ICF URI",
    "AF": "ICF Comments / Considerations",
    "AG": "ICF Relationship",
    "AH": "SNOMED GPS Code",
    "AI": "SNOMED GPS Comments / Considerations",
    "AJ": "SNOMED GPS Relationship",
    # Only on the ``all`` worksheet, which copies every topic worksheet verbatim and adds
    # this column to record which tab a row came from. It says where the row sits, not
    # what it says, which is why it is structural and why suppressing it is what makes
    # the copy collapse onto its original.
    "AK": "Tab",
}

_DATA_DICTIONARY_SCHEMA = TableSchema(
    schema_id="who-dak-2-data-dictionary",
    row_kind=PassageKind.DATA_DICTIONARY_ENTRY,
    labels=_DATA_DICTIONARY_LABELS,
    structural_columns=frozenset({"AK"}),
    display_columns=("A", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O"),
    collapsed=(
        CollapsedGroup(
            label="Terminology",
            columns=("P", "T", "W", "Z", "AD", "AH"),
        ),
    ),
    identity_columns=("B", "C"),
)

_SERVICE_SCHEDULE_SCHEMA = TableSchema(
    schema_id="who-dak-2-service-schedule",
    row_kind=PassageKind.SCHEDULE_ENTRY,
    labels={
        "B": "Service Name",
        "C": "Service Description",
        "D": "Trigger Event",
        "E": "Trigger Date",
        "F": "Create Condition",
        "G": "Due Date",
        "H": "Overdue",
        "I": "Expiration",
        "J": "Completion",
        "K": "Potential risks and alternative schedules",
        "L": "Comments",
        "M": "Reference(s)",
    },
    identity_columns=("B",),
)

_INDICATOR_DEFINITION_SCHEMA = TableSchema(
    schema_id="who-dak-2-indicator-definitions",
    row_kind=PassageKind.INDICATOR_DEFINITION,
    labels={
        "A": "DAK ID",
        "B": "Ref no.",
        "C": "Short name",
        "D": "Indicator definition",
        "E": "Numerator calculation",
        "F": "Numerator exclusions",
        "G": "Denominator calculation",
        "H": "Denominator exclusions",
        "I": "Disaggregation data elements",
        "J": "List of all data elements included in numerator and denominator",
        "K": "Numerator definition",
        "L": "Denominator definition",
        "M": "Disaggregation description",
        "N": "Comments and references",
        "O": "Reference",
        "P": "Page no.",
        "Q": "GAM2023 alignment",
        "R": "GF2022 alignment",
        "S": "MER2.6.1 alignment",
        "T": "WHO2020 alignment",
        "U": "Survey based",
        "V": "Included in DAK",
        "W": "Priority",
        "X": "Core",
        "Y": "New",
        "Z": "Updated",
        "AA": "Category",
        "AB": "What it measures",
        "AC": "Rationale",
        "AD": "Method of measurement",
    },
    display_columns=(
        "B",
        "D",
        "K",
        "L",
        "E",
        "F",
        "G",
        "H",
        "M",
        "AB",
        "AC",
        "AD",
        "N",
        "O",
        "P",
    ),
    identity_columns=("A", "C"),
)

_GAM_SCHEMA = TableSchema(
    schema_id="who-dak-2-indicator-gam2023",
    row_kind=PassageKind.INDICATOR_DEFINITION,
    labels={
        "A": "Ref no.",
        "B": "Short name",
        "C": "Indicator definition",
        "D": "Numerator",
        "E": "Denominator",
        "F": "Measurement frequency",
    },
    identity_columns=("A", "B"),
)

_GF_SCHEMA = TableSchema(
    schema_id="who-dak-2-indicator-gf2023",
    row_kind=PassageKind.INDICATOR_DEFINITION,
    labels={
        "A": "Indicator code",
        "B": "Module",
        "C": "Indicator description",
        "D": "Numerator definition",
        "E": "Denominator definition",
        "F": "Disaggregation of reported results",
        "G": "Reporting frequency",
        "H": "Number",
        "I": "Changes to previous GF release",
        "J": "Reference",
    },
    identity_columns=("A",),
)

_MER_SCHEMA = TableSchema(
    schema_id="who-dak-2-indicator-mer261",
    row_kind=PassageKind.INDICATOR_DEFINITION,
    labels={
        "A": "Indicator code",
        "B": "Indicator group",
        "C": "Indicator description",
        "D": "Numerator definition",
        "E": "Denominator definition",
        "F": "Reporting frequency",
        "G": "Changes to FY23",
        "H": "Number",
    },
    identity_columns=("A",),
)

_WHO2020_SCHEMA = TableSchema(
    schema_id="who-dak-2-indicator-who2020",
    row_kind=PassageKind.INDICATOR_DEFINITION,
    labels={
        "A": "Indicator code",
        "B": "Category",
        "C": "Short name",
        "D": "Indicator description",
        "E": "Numerator definition",
        "F": "Denominator definition",
        "G": "Additional",
    },
    identity_columns=("A", "C"),
)

_DATA_DICTIONARY_WORKSHEETS = (
    "HIV.A Registration",
    "HIV.B HTS visit",
    "HIV.C PrEP visit",
    "HIV.Configuration",
    "HIV.D Care-Treatment",
    "HIV.D HIV-TB",
    "HIV.E-F PMTCT",
    "HIV.G Diagnostics",
    "HIV.H Follow-up",
    "HIV.I Referral",
    "HIV.Prevention",
    "HIV.Surveillance",
    "all",
)
_SERVICE_SCHEDULE_WORKSHEETS = (
    "HIV.S.1 Recommended Services",
    "HIV.S.2 Monitoring ART response",
    "HIV.S.3 CCA screening",
    "HIV.S.4 HIV retesting",
)

DECLARED_TABLE_SCHEMAS: Mapping[tuple[str, str], TableSchema] = {
    **{(_ANNEX_A, sheet): _DATA_DICTIONARY_SCHEMA for sheet in _DATA_DICTIONARY_WORKSHEETS},
    **{(_ANNEX_B, sheet): _SERVICE_SCHEDULE_SCHEMA for sheet in _SERVICE_SCHEDULE_WORKSHEETS},
    (_ANNEX_C, "Indicator definitions"): _INDICATOR_DEFINITION_SCHEMA,
    (_ANNEX_C, "GAM2023"): _GAM_SCHEMA,
    (_ANNEX_C, "GF2023"): _GF_SCHEMA,
    (_ANNEX_C, "MER2.6.1"): _MER_SCHEMA,
    (_ANNEX_C, "WHO2020"): _WHO2020_SCHEMA,
}


def discover_table_schemas(
    records: Iterable[CorpusEvidenceRecord],
) -> dict[tuple[str, str], TableSchema]:
    """Read decision-table column labels out of the release being served.

    A WHO decision table's header row was approved as evidence, and it is identifiable
    without guessing: exactly one row per table carries a cell whose value is the
    publisher's own ``Output Type`` label, and that row names every column. Using it
    beats declaring seventeen worksheet layouts, and it cannot drift from the release,
    because it *is* the release.

    A table with more than one candidate header is skipped rather than resolved. An
    ambiguous header would attach the wrong label to a clinical condition, which is worse
    than showing the row unlabelled.
    """

    candidates: dict[tuple[str, str], list[tuple[int, tuple[TableCell, ...]]]] = {}
    for record in records:
        anchor = table_anchor(record)
        if anchor is None:
            continue
        cells = parse_spreadsheet_row(record.content_exact)
        if cells is None:
            continue
        if not any(cell.value.strip() == _DECISION_OUTCOME_LABEL for cell in cells):
            continue
        candidates.setdefault((record.source_version_id, anchor[0]), []).append(
            (anchor[1], cells)
        )

    discovered: dict[tuple[str, str], TableSchema] = {}
    for key, found in candidates.items():
        if len(found) != 1:
            continue
        header_row, cells = found[0]
        ordered = sorted(cells, key=lambda cell: cell.ordinal)
        labels = {cell.column: cell.value.strip() for cell in ordered if cell.value.strip()}
        outcome_column = next(
            (
                cell.column
                for cell in ordered
                if cell.value.strip() == _DECISION_OUTCOME_LABEL
            ),
            None,
        )
        discovered[key] = TableSchema(
            schema_id=f"discovered:{key[1]}",
            row_kind=PassageKind.DECISION_RULE,
            labels=labels,
            # The leading column holds the rule identifier, headed ``R`` in every WHO
            # decision table. It names the row rather than stating a condition.
            identity_columns=(ordered[0].column,) if ordered else (),
            outcome_column=outcome_column,
            header_row=header_row,
        )
    return discovered


class PassagePresenter:
    """Renders approved evidence records for a reader and for the generation lane."""

    def __init__(self, schemas: Mapping[tuple[str, str], TableSchema] | None = None) -> None:
        self._schemas: dict[tuple[str, str], TableSchema] = dict(DECLARED_TABLE_SCHEMAS)
        if schemas:
            self._schemas.update(schemas)

    @classmethod
    def for_release(cls, records: Iterable[CorpusEvidenceRecord]) -> PassagePresenter:
        """Declared schemas plus every header the release itself carries."""

        return cls(discover_table_schemas(records))

    def schema_for(self, record: CorpusEvidenceRecord) -> TableSchema | None:
        anchor = table_anchor(record)
        if anchor is None:
            return None
        return self._schemas.get((record.source_version_id, anchor[0]))

    def render(self, record: CorpusEvidenceRecord) -> RenderedPassage:
        cells = parse_spreadsheet_row(record.content_exact)
        anchor = table_anchor(record)
        if cells is None or anchor is None:
            # Page text from a PDF, or any content the extractor did not cell-address.
            # It is already prose; normalising whitespace is the whole of the view.
            body = _normalize(record.content_exact)
            return RenderedPassage(
                evidence_id=record.evidence_id,
                kind=PassageKind.NARRATIVE,
                title="",
                body=body,
                fingerprint=_fingerprint([body.casefold()]),
            )

        table_id, row_index = anchor
        schema = self._schemas.get((record.source_version_id, table_id))
        ordered = sorted(cells, key=lambda cell: cell.ordinal)
        kind = _classify_row(ordered, schema, row_index)
        title = _title(table_id, kind, ordered, schema)
        body = _body(ordered, schema, kind)
        return RenderedPassage(
            evidence_id=record.evidence_id,
            kind=kind,
            title=title,
            body=body,
            fingerprint=_row_fingerprint(ordered, schema),
            schema_id=schema.schema_id if schema is not None else None,
        )


def _substantive(cells: tuple[TableCell, ...]) -> tuple[TableCell, ...]:
    return tuple(cell for cell in cells if not is_placeholder(cell.value))


def _classify_row(
    cells: tuple[TableCell, ...],
    schema: TableSchema | None,
    row_index: int,
) -> PassageKind:
    if schema is not None and schema.header_row == row_index:
        return PassageKind.TABLE_HEADER
    filled = _substantive(cells)
    if len(filled) == 2 and filled[0].value.strip().casefold() in _TABLE_CAPTION_KEYS:
        return PassageKind.TABLE_METADATA
    if len(filled) == 1:
        value = filled[0].value.strip()
        if "\n" not in value and len(value) <= _SECTION_TITLE_MAX_CHARS:
            return PassageKind.TABLE_SECTION_TITLE
        return PassageKind.TABLE_NOTE
    if schema is None:
        return PassageKind.UNLABELLED_TABLE_ROW
    return schema.row_kind


def _title(
    table_id: str,
    kind: PassageKind,
    cells: tuple[TableCell, ...],
    schema: TableSchema | None,
) -> str:
    descriptor = _KIND_TITLES.get(kind, "row")
    identity: list[str] = []
    if schema is not None and kind is schema.row_kind:
        values = {cell.column: cell.value.strip() for cell in cells}
        identity = [
            values[column]
            for column in schema.identity_columns
            if values.get(column) and not is_placeholder(values[column])
        ]
    named = " ".join(identity)
    return f"{table_id} - {descriptor} {named}".strip() if named else f"{table_id} - {descriptor}"


def _display_order(cells: tuple[TableCell, ...], schema: TableSchema) -> tuple[str, ...]:
    if schema.display_columns:
        return schema.display_columns
    collapsed = {column for group in schema.collapsed for column in group.columns}
    skip = set(schema.identity_columns) | schema.structural_columns | collapsed
    return tuple(cell.column for cell in cells if cell.column not in skip)


def _body(
    cells: tuple[TableCell, ...],
    schema: TableSchema | None,
    kind: PassageKind,
) -> str:
    values = {cell.column: cell.value.strip() for cell in cells}
    filled = _substantive(cells)

    if kind is PassageKind.TABLE_HEADER:
        return "Columns: " + " | ".join(cell.value.strip() for cell in filled)
    if kind in {PassageKind.TABLE_SECTION_TITLE, PassageKind.TABLE_NOTE}:
        return _normalize(filled[0].value) if filled else ""
    if kind is PassageKind.TABLE_METADATA:
        return ": ".join(_normalize(cell.value) for cell in filled)
    if schema is None:
        # No labels available. Dropping the coordinates is still the difference between
        # unreadable and readable, so the values are joined in sheet order.
        return "\n".join(f"- {_normalize(cell.value)}" for cell in filled)

    lines: list[str] = []
    order = _display_order(cells, schema)
    outcome_ordinal = (
        column_ordinal(schema.outcome_column) if schema.outcome_column else None
    )
    condition_lines: list[str] = []
    outcome_lines: list[str] = []
    for column in order:
        value = values.get(column, "")
        if not value or is_placeholder(value):
            continue
        label = schema.label_for(column) or column
        rendered = f"- {label}: {_normalize(value)}"
        if outcome_ordinal is not None and column_ordinal(column) < outcome_ordinal:
            condition_lines.append(rendered)
        elif outcome_ordinal is not None:
            outcome_lines.append(rendered)
        else:
            lines.append(rendered)

    if outcome_ordinal is not None:
        if condition_lines:
            lines.append("When:")
            lines.extend(condition_lines)
        if outcome_lines:
            lines.append("Then:")
            lines.extend(outcome_lines)

    for group in schema.collapsed:
        parts = [
            f"{_short_label(schema.label_for(column) or column)} {values[column]}"
            for column in group.columns
            if values.get(column) and not is_placeholder(values[column])
        ]
        if parts:
            lines.append(f"- {group.label}: {'; '.join(parts)}")
    return "\n".join(lines)


def _short_label(label: str) -> str:
    return label[: -len(" Code")] if label.endswith(" Code") else label


def _row_fingerprint(cells: tuple[TableCell, ...], schema: TableSchema | None) -> str:
    """Identify a row by what it says, not by where it sits.

    Column letters are part of the identity - a value in a different column is a
    different fact - but columns a schema declares structural are not, because that is
    precisely what a column recording which worksheet a copied row came from is.
    """

    structural = schema.structural_columns if schema is not None else frozenset()
    return _fingerprint(
        f"{cell.column}={_normalize(cell.value).casefold()}"
        for cell in cells
        if cell.column not in structural
    )


__all__ = [
    "DECLARED_TABLE_SCHEMAS",
    "RECOMMENDATION_BEARING_KINDS",
    "CollapsedGroup",
    "PassageKind",
    "PassagePresenter",
    "RenderedPassage",
    "TableCell",
    "TableSchema",
    "column_ordinal",
    "discover_table_schemas",
    "is_placeholder",
    "parse_spreadsheet_row",
    "table_anchor",
]
