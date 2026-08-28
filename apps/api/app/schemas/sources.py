"""The source-inspection contract.

Separate from `questions` because these are answers about a *source*, not about a run: a
reader who has an evidence record in front of them asking what sits around it in the
document it came from. They carry `EvidenceDetail` because a neighbouring row is an
evidence record like any other, and describing it any other way would give the interface
a second, weaker notion of provenance to render.
"""

from pydantic import Field

from app.schemas.questions import ApiContractModel, EvidenceDetail


class TableRowNeighbour(ApiContractModel):
    """One row of a table, positioned relative to the row that was asked about."""

    #: Zero-based, as the corpus extractor records it.
    row_index: int = Field(ge=0)
    #: One-based, as the spreadsheet application numbers it for a reader.
    row_number: int = Field(ge=1)
    #: True for the row the caller anchored on, so a client never has to re-derive it.
    is_anchor_row: bool
    evidence: EvidenceDetail


class TableRowNeighbourhood(ApiContractModel):
    """The rows immediately around an anchored row of one table.

    An empty `rows` is an ordinary answer, not an error: the release may hold no
    neighbours, the table may not be in it, or the source may not be servable. The caller
    is told the same thing in each case, for the reason `sources.py` gives.
    """

    source_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    anchor_row_index: int = Field(ge=0)
    radius: int = Field(ge=0)
    rows: list[TableRowNeighbour] = Field(default_factory=list)
