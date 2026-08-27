"""Bounded deterministic query expansion for retrieval safety lanes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

EXACT_TERMINOLOGY_REVISION = "exact-terminology-unicode-v1"
SAFETY_QUERY_REVISION = "clinical-safety-query-v1"
CONFLICT_AWARE_QUERY_REVISION = "clinical-safety-query-v2"
MAX_EXPANSIONS_PER_LANE = 8
MAX_EXPANSION_CHARACTERS = 8_192
# A variant made only of punctuation carries no searchable term. Lexical backends reject
# such input outright, so one degenerate fragment would otherwise fail an entire run.
_SEARCHABLE_TERM = re.compile(r"[^\W_]", re.UNICODE)

_CODE_PATTERN = re.compile(
    r"(?<![\w.])(?:[A-Za-z][A-Za-z0-9_-]*)(?:\.[A-Za-z0-9_-]+)+(?![\w.])"
)
_QUOTED_PATTERN = re.compile(r'"([^"\r\n]{2,200})"|\'([^\'\r\n]{2,200})\'')
_TERMINOLOGY_PREFIX = re.compile(
    r"^\s*retrieve\s+the\s+source\s+(?:definition|mapping)(?:\s+or\s+(?:definition|mapping))?"
    r"\s+for\s+these\s+exact\s+terms\s*:\s*",
    re.IGNORECASE,
)
_CONFLICT_PREFIX = re.compile(
    r"^\s*retrieve\s+both\s+source\s+passages\s+needed\s+to\s+inspect\s+"
    r"a\s+potential\s+conflict\s*:\s*",
    re.IGNORECASE,
)
_CLAUSE_BOUNDARY = re.compile(r"(?:[.;]|\b(?:but|however|unless|except|whereas)\b)", re.I)
_POLARITY_CLAUSE = re.compile(
    r"\b(?:no|not|without|unless|except|contraindicat\w*|avoid\w*|"
    r"must(?:n't|\s+not)|should(?:n't|\s+not)|do(?:n't|\s+not)|"
    r"only\s+if|required\s+if|greater\s+than|less\s+than|at\s+least)\b",
    re.I,
)
_SAFETY_VOCABULARY: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:applicab|eligib|population|who\s+should|when\s+should|indicat)",
            re.I,
        ),
        "applicability eligibility inclusion exclusion target population",
    ),
    (
        re.compile(r"\b(?:contraindicat|avoid|do\s+not|must\s+not|harm|adverse)", re.I),
        "contraindication avoid exclusion warning adverse effect",
    ),
    (
        re.compile(r"\b(?:dose|dosage|threshold|maximum|minimum|mg|ml)\b", re.I),
        "dose dosage threshold minimum maximum adjustment",
    ),
    (
        re.compile(r"\b(?:monitor|follow[- ]?up|surveillance|reassess|test\s+again)", re.I),
        "monitoring follow-up surveillance reassessment interval",
    ),
    (
        re.compile(r"\b(?:jurisdiction|country|national|regional|local)\b", re.I),
        "jurisdiction country national regional local applicability",
    ),
    (
        re.compile(r"\b(?:current|latest|fresh|updated|supersed|withdrawn|version)\b", re.I),
        "current latest update superseded withdrawn version effective date",
    ),
)


class QueryExpansionKind(str, Enum):
    EXACT_TERMINOLOGY = "EXACT_TERMINOLOGY"
    SAFETY_QUERY = "SAFETY_QUERY"


class QueryExpansionOrigin(str, Enum):
    """Why an expansion was emitted, so consumers never parse lane identifiers."""

    CONFLICT_SIDE = "CONFLICT_SIDE"
    POLARITY_CLAUSE = "POLARITY_CLAUSE"
    CONTROLLED_VOCABULARY = "CONTROLLED_VOCABULARY"
    TERMINOLOGY_TERM = "TERMINOLOGY_TERM"


class QueryExpansionError(ValueError):
    """Raised for an unknown revision or an expansion outside hard bounds."""


@dataclass(frozen=True)
class ExpandedQuery:
    lane_id: str
    kind: QueryExpansionKind
    text: str
    origin: QueryExpansionOrigin


def is_conflict_query(question: str) -> bool:
    """Return whether the query uses the explicit production conflict grammar."""

    if not isinstance(question, str):
        return False
    body = _CONFLICT_PREFIX.sub("", question).strip()
    return body != question.strip() and " | " in body


class DeterministicQueryExpander:
    """Generate reproducible lexical variants without generative query drift."""

    def __init__(
        self,
        *,
        terminology_revision: str | None,
        safety_query_revision: str | None,
    ) -> None:
        if terminology_revision not in (None, EXACT_TERMINOLOGY_REVISION):
            raise QueryExpansionError(
                f"unsupported terminology revision: {terminology_revision}"
            )
        if safety_query_revision not in (
            None,
            SAFETY_QUERY_REVISION,
            CONFLICT_AWARE_QUERY_REVISION,
        ):
            raise QueryExpansionError(
                f"unsupported safety-query revision: {safety_query_revision}"
            )
        self._terminology_enabled = terminology_revision is not None
        self._safety_enabled = safety_query_revision is not None
        self._conflict_aware = safety_query_revision == CONFLICT_AWARE_QUERY_REVISION

    def expand(self, question: str) -> tuple[ExpandedQuery, ...]:
        if not isinstance(question, str) or not question.strip():
            raise QueryExpansionError("query expansion requires non-empty text")
        expansions: list[ExpandedQuery] = []
        if self._terminology_enabled:
            expansions.extend(self._terminology_queries(question))
        if self._safety_enabled:
            expansions.extend(
                self._safety_queries(question, conflict_aware=self._conflict_aware)
            )
        return self._deduplicate_and_bound(question, expansions)

    @staticmethod
    def _terminology_queries(question: str) -> list[ExpandedQuery]:
        codes = tuple(dict.fromkeys(_CODE_PATTERN.findall(question)))
        quoted = tuple(
            dict.fromkeys(left or right for left, right in _QUOTED_PATTERN.findall(question))
        )
        variants: list[str] = []
        stripped = _TERMINOLOGY_PREFIX.sub("", question).strip()
        if stripped != question.strip():
            variants.append(stripped)
        if codes:
            variants.append(" ".join(codes))
            variants.extend(codes)
        variants.extend(quoted)
        return [
            ExpandedQuery(
                lane_id=f"exact-terminology-{position}",
                kind=QueryExpansionKind.EXACT_TERMINOLOGY,
                text=text,
                origin=QueryExpansionOrigin.TERMINOLOGY_TERM,
            )
            for position, text in enumerate(variants, start=1)
        ]

    @staticmethod
    def _safety_queries(
        question: str, *, conflict_aware: bool = False
    ) -> list[ExpandedQuery]:
        variants: list[tuple[str, QueryExpansionOrigin]] = []
        conflict_body = _CONFLICT_PREFIX.sub("", question).strip()
        if is_conflict_query(question):
            sides = tuple(part.strip() for part in conflict_body.split(" | ") if part.strip())
            variants.extend((side, QueryExpansionOrigin.CONFLICT_SIDE) for side in sides)
            if conflict_aware:
                for side in sides:
                    clauses = tuple(
                        clause.strip(" -")
                        for clause in _CLAUSE_BOUNDARY.split(side)
                        if clause.strip(" -")
                    )
                    variants.extend(
                        (clause, QueryExpansionOrigin.POLARITY_CLAUSE)
                        for clause in clauses
                        if _POLARITY_CLAUSE.search(clause)
                    )
        for pattern, controlled_terms in _SAFETY_VOCABULARY:
            if pattern.search(question):
                variants.append(
                    (
                        f"{question.strip()} {controlled_terms}",
                        QueryExpansionOrigin.CONTROLLED_VOCABULARY,
                    )
                )
        return [
            ExpandedQuery(
                lane_id=f"safety-query-{position}",
                kind=QueryExpansionKind.SAFETY_QUERY,
                text=text,
                origin=origin,
            )
            for position, (text, origin) in enumerate(variants, start=1)
        ]

    @staticmethod
    def _deduplicate_and_bound(
        original: str,
        expansions: list[ExpandedQuery],
    ) -> tuple[ExpandedQuery, ...]:
        original_key = " ".join(original.split()).casefold()
        seen = {original_key}
        counts = {kind: 0 for kind in QueryExpansionKind}
        output: list[ExpandedQuery] = []
        for expansion in expansions:
            normalized = " ".join(expansion.text.split())
            if not normalized or not _SEARCHABLE_TERM.search(normalized):
                continue
            if len(normalized) > MAX_EXPANSION_CHARACTERS:
                normalized = normalized[:MAX_EXPANSION_CHARACTERS].rstrip()
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            if counts[expansion.kind] >= MAX_EXPANSIONS_PER_LANE:
                continue
            counts[expansion.kind] += 1
            output.append(
                ExpandedQuery(
                    lane_id=(
                        f"{expansion.kind.value.lower().replace('_', '-')}-"
                        f"{counts[expansion.kind]}"
                    ),
                    kind=expansion.kind,
                    text=normalized,
                    origin=expansion.origin,
                )
            )
        return tuple(output)
