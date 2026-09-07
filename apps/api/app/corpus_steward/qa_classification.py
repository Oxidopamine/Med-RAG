"""Deterministic, fail-closed classification for Phase 4 evidence QA."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

from app.corpus_steward.embedding_adapters import unicode_medical_tokens
from app.corpus_steward.materialization_schemas import (
    MaterializedEvidenceRecord,
)
from app.corpus_steward.qa_schemas import (
    EvidenceQADecision,
    QAClassificationInput,
    QAClassificationInputItem,
    QADecisionBatchInput,
    QADisposition,
    QAQuarantineReason,
)
from app.schemas.corpus import (
    ApplicabilityScope,
    EvidenceRole,
    RecommendationGrade,
)
from app.schemas.domain import utc_now

QA_CLASSIFIER_NAME = "phase4-deterministic-classifier"
QA_CLASSIFIER_VERSION = "1.0.0"


_XLSX_UNIT = re.compile(r"^xlsx:(?P<sheet>.*):row:(?P<row>\d+)$", re.IGNORECASE)
_PDF_UNIT = re.compile(r"^pdf:page:(?P<page>\d+)$", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_THRESHOLD = re.compile(
    r"(?:[<>]=?|≤|≥)\s*\d|\b(?:at least|at most|less than|greater than|within)\s+\d|"
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|copies/ml|cells/mm|days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)
_EXCEPTION = re.compile(
    r"\b(?:contraindicat\w*|ineligible|not eligible|unless|do not|should not|must not|"
    r"not recommended|avoid(?:ed|ance)?|exclusion criter\w*)\b",
    re.IGNORECASE,
)
_MONITORING = re.compile(
    r"\b(?:monitor\w*|follow[- ]?up|viral load|test result|laborator\w*|indicator|"
    r"numerator|denominator|screen(?:ing)?|surveillance)\b",
    re.IGNORECASE,
)
_ADMIN_SHEETS = frozenset(
    {
        "cover",
        "readme",
        "search",
        "references",
        "example decision tables",
    }
)
_HEADER_PREFIXES = (
    "activity id data element id data element label",
    "hit policy indicator rule order",
    "hit policy indicator first",
    "service name service description trigger event",
    "the name of the service for which the schedule is relevant",
    "sheet name description",
    "requirement id activity id and description",
    "requirement id category non-functional requirement",
    "reference/source activity id & activity name decision table id",
    "ref. no. short name indicator description",
    "ref no short name indicator description",
    "output type action guidance annotations",
)
_METADATA_PREFIXES = (
    "decision id ",
    "business rule ",
    "trigger ",
    "hit policy ",
)
_PDF_ADMIN_PREFIXES = (
    "sales, rights and licensing",
    "contents",
    "acknowledgements",
    "list of abbreviations",
    "references",
    "for more information, contact:",
)

_POPULATION_PATTERNS = (
    (r"\bpeople living with hiv\b|\bpeople with hiv\b", "people living with HIV"),
    (r"\bhiv-positive\b|\bhiv positive\b", "people with HIV-positive status"),
    (r"\bhiv-negative\b|\bhiv negative\b", "people with HIV-negative status"),
    (r"\badolescents?\b", "adolescents"),
    (r"\bchildren\b|\bchild\b", "children"),
    (r"\binfants?\b", "infants"),
    (r"\badults?\b", "adults"),
    (r"\bpregnan(?:t|cy)\b", "pregnant people"),
    (r"\bbreastfeed\w*\b", "breastfeeding people"),
    (r"\btransgender\b", "transgender people"),
    (r"\bsex workers?\b", "sex workers"),
    (r"\bpeople who inject drugs\b", "people who inject drugs"),
    (r"\bpartners?\b", "partners of people receiving HIV services"),
)
_CARE_SETTING_PATTERNS = (
    (r"\bprimary health care\b", "primary health care"),
    (r"\bantenatal care\b|\banc\b", "antenatal care"),
    (r"\bpostnatal\b|\bpostpartum\b", "postnatal care"),
    (r"\bcommunit(?:y|ies)\b", "community-based care"),
    (r"\bfacilit(?:y|ies)\b", "health facilities"),
    (r"\blaborator\w*\b|\bdiagnostic\w*\b", "laboratory and diagnostic services"),
    (r"\bhiv testing services?\b|\bhts\b", "HIV testing services"),
    (r"\bprep\b|\bpep\b", "HIV prevention services"),
    (r"\btb services?\b|\btuberculosis\b", "tuberculosis services"),
)


# Narrative block-grouped units, the shape `NarrativeSourceExtractor` emits.
_NARRATIVE_UNIT = re.compile(r"^pdf:page:(?P<page>\d+):block:(?P<block>\d+)$", re.IGNORECASE)

# Both GRADE registers. "We recommend" is strong and "we suggest" is conditional, and a
# vocabulary carrying only the first silently discards every conditional recommendation in
# the corpus - measured on the WHO 2021 consolidated guidelines, that was 8 of 165 known
# recommendations and the whole difference between recall 0.952 and 1.000. See
# docs/narrative-role-classification.md.
_DEONTIC = re.compile(
    r"\b(should|must|shall|is recommended|are recommended|recommends?|suggests?|"
    r"be offered|be provided|be given|be started|be initiated|be used|be considered|"
    r"may be offered|is advised)\b",
    re.IGNORECASE,
)
# Structure that is never a recommendation however it is worded. Shape, never meaning -
# a rule that decided what a passage *means* would be the relevance judgement D5 refuses.
_NARRATIVE_NON_EVIDENCE = re.compile(
    r"^\s*(contents|table of contents|acknowledgements?|abbreviations|acronyms|"
    r"references|bibliography|annex(?:es)?|appendix|glossary|foreword|preface|"
    r"list of (?:tables|figures|boxes|contributors)|isbn|sales, rights|"
    r"web annex|declarations? of interest)\b",
    re.IGNORECASE,
)
_REFERENCE_ENTRY = re.compile(
    r"^\s*\d{1,3}\.\s+\S.{0,80}?\b(et al|WHO|Geneva|doi:|https?://)", re.IGNORECASE
)
# A recommendation is a statement, not a caption or a running fragment. The same floor the
# published sampling frame used, so the classifier and the measurement agree about shape.
_MIN_STATEMENT_CHARS = 120

# Population and care-setting language read from the passage itself. The DAK path matches a
# fixed HIV vocabulary, which on any other disease area yields no population, no
# APPLICABILITY role, and a role gate that can never pass.
_NARRATIVE_POPULATION = re.compile(
    r"\b(adults?|adolescents?|children|infants?|neonates?|newborns?|older people|"
    r"pregnant (?:people|women)|breastfeeding (?:people|women)|"
    r"people (?:living )?with [a-z][a-z\- ]{2,40}?|patients? with [a-z][a-z\- ]{2,40}?|"
    r"women|men|people who [a-z ]{3,30})\b",
    re.IGNORECASE,
)
_NARRATIVE_SETTING = re.compile(
    r"\b(primary (?:health )?care|secondary care|tertiary care|community(?:-based)? "
    r"(?:care|settings?)|outpatient (?:care|clinics?|settings?)|inpatient (?:care|settings?)|"
    r"health facilit(?:y|ies)|hospitals?|antenatal care|postnatal care|"
    r"laboratory services|emergency (?:care|departments?)|low-resource settings?)\b",
    re.IGNORECASE,
)


def automated_decision_batch(
    classification_input: QAClassificationInput,
    materialized: dict[str, MaterializedEvidenceRecord],
    *,
    materialization_candidate_id: str,
    decision_authority: str = f"{QA_CLASSIFIER_NAME}@{QA_CLASSIFIER_VERSION}",
    decided_at: datetime | None = None,
) -> QADecisionBatchInput:
    """Classify and seal every materialized record without operator input."""

    items = {item.evidence_id: item for item in classification_input.items}
    if len(items) != len(classification_input.items):
        raise ValueError("classification input evidence IDs must be unique")
    if classification_input.corpus_release_candidate_id != materialization_candidate_id:
        raise ValueError(
            "classification input and materialization identify different corpus candidates"
        )
    if set(items) != set(materialized):
        missing = len(set(items) - set(materialized))
        extra = len(set(materialized) - set(items))
        raise ValueError(
            "classification input and materialization membership differ "
            f"(missing={missing}, extra={extra})"
        )
    if classification_input.evidence_count != len(items):
        raise ValueError("classification input evidence count is inconsistent")
    replay_passed = sum(item.replay_passed for item in items.values())
    if (
        classification_input.replay_passed_count != replay_passed
        or classification_input.replay_failed_count != len(items) - replay_passed
    ):
        raise ValueError("classification input replay accounting is inconsistent")

    proposals: dict[str, _Proposal] = {}
    for evidence_id, item in items.items():
        record = materialized[evidence_id]
        if record.evidence_sha256 != item.materialized_evidence_sha256:
            raise ValueError(f"classification input evidence digest mismatch: {evidence_id}")
        if (
            record.content.asset_id != item.asset_id
            or record.content.source_unit_id != item.source_unit_id
        ):
            raise ValueError(f"classification input source binding mismatch: {evidence_id}")
        proposals[evidence_id] = _classify(item, record)
    _apply_exact_deduplication(proposals, materialized)
    decisions = {evidence_id: proposal.decision for evidence_id, proposal in proposals.items()}
    _validate_final_decisions(items, materialized, decisions)
    return QADecisionBatchInput(
        corpus_release_candidate_id=classification_input.corpus_release_candidate_id,
        decision_authority=decision_authority,
        decided_at=decided_at or utc_now(),
        decisions=tuple(decisions.values()),
    )


class _Proposal:
    def __init__(self, decision: EvidenceQADecision, rules: tuple[str, ...]) -> None:
        self.decision = decision
        self.rules = tuple(sorted(set(rules)))


def _classify(
    item: QAClassificationInputItem, record: MaterializedEvidenceRecord
) -> _Proposal:
    content = record.content
    text = _SPACE.sub(" ", content.content_search).strip()
    lowered = text.casefold()
    rules: list[str] = []
    reasons: list[QAQuarantineReason] = []

    if not item.replay_passed:
        rules.append("anchor-replay-failed")
        reasons.append(QAQuarantineReason.ANCHOR_REPLAY_FAILED)
        if "object at 0x" in lowered or "arrayformula" in lowered:
            rules.append("unstable-spreadsheet-formula")
            reasons.append(QAQuarantineReason.EXTRACTION_STRUCTURE_FAILED)
        return _quarantine(item, reasons, rules)

    if "object at 0x" in lowered or "arrayformula" in lowered:
        return _quarantine(
            item,
            [QAQuarantineReason.EXTRACTION_STRUCTURE_FAILED],
            ["unstable-spreadsheet-formula"],
        )

    # A unit that tokenizes to nothing carries no retrievable signal and cannot be
    # indexed: the sparse adapter refuses it outright ("BM25 input contains no
    # searchable terms after tokenization"), and BM25 release statistics refuse the
    # whole release over it. Approving such a unit therefore produces a release that
    # looks valid and cannot be built. The WHO composite carries three of them - PDF
    # table-of-contents dot leaders extracted as RATIONALE evidence - and they blocked
    # vector production 9,240 records in. The same tokenizer the adapter uses decides
    # it here, so QA and the encoder cannot disagree about what is searchable.
    if not unicode_medical_tokens(text):
        return _quarantine(
            item,
            [QAQuarantineReason.NON_EVIDENCE],
            ["content-free-unit"],
        )

    administrative = _administrative_classification(
        content.asset_id, content.source_unit_id, lowered, len(text)
    )
    if administrative is not None:
        reason, rule = administrative
        return _quarantine(item, [reason], [rule])

    narrative = bool(_NARRATIVE_UNIT.match(content.source_unit_id))
    roles, base_rule = _base_roles(content.asset_id, content.source_unit_id, text)
    if not roles:
        return _quarantine(
            item,
            [QAQuarantineReason.CLINICAL_CLASSIFICATION_UNRESOLVED],
            [base_rule],
        )
    rules.append(base_rule)

    applicability = _narrative_applicability(text) if narrative else _applicability(text)
    if any(
        (
            applicability.population,
            applicability.care_settings,
            applicability.inclusion_criteria,
            applicability.exclusion_criteria,
        )
    ):
        roles.add(EvidenceRole.APPLICABILITY)
        rules.append("explicit-applicability-language")
    if _THRESHOLD.search(text):
        roles.add(EvidenceRole.DOSE_OR_THRESHOLD)
        rules.append("explicit-dose-or-threshold-language")
    if _EXCEPTION.search(text):
        roles.add(EvidenceRole.EXCEPTION_OR_CONTRAINDICATION)
        rules.append("explicit-exception-language")
    if _MONITORING.search(text):
        roles.add(EvidenceRole.MONITORING)
        rules.append("explicit-monitoring-language")

    grade = _recommendation_grade(text)
    if grade is not None:
        rules.append("explicit-who-grade-language")
    decision = EvidenceQADecision(
        evidence_id=item.evidence_id,
        materialized_evidence_sha256=item.materialized_evidence_sha256,
        disposition=QADisposition.APPROVE,
        evidence_roles=tuple(roles),
        applicability=applicability,
        recommendation_grade=grade,
        notes=f"Deterministic classification basis: {', '.join(sorted(rules))}.",
    )
    return _Proposal(decision, tuple(rules))


def _administrative_classification(
    asset_id: str,
    source_unit_id: str,
    lowered: str,
    text_length: int,
) -> tuple[QAQuarantineReason, str] | None:
    xlsx = _XLSX_UNIT.match(source_unit_id)
    if xlsx:
        sheet = xlsx.group("sheet").strip().casefold()
        if sheet in _ADMIN_SHEETS:
            return QAQuarantineReason.NON_EVIDENCE, "administrative-worksheet"
        if asset_id.endswith("ANNEX_D"):
            return QAQuarantineReason.NON_EVIDENCE, "software-requirement-not-clinical-evidence"
        if lowered.startswith(_HEADER_PREFIXES):
            return QAQuarantineReason.HEADER_OR_FOOTER, "spreadsheet-column-header"
        if lowered.startswith(_METADATA_PREFIXES) and text_length < 500:
            return QAQuarantineReason.NON_EVIDENCE, "decision-table-metadata-row"

    pdf = _PDF_UNIT.match(source_unit_id)
    if pdf:
        page = int(pdf.group("page"))
        if lowered.startswith(_PDF_ADMIN_PREFIXES):
            return QAQuarantineReason.NON_EVIDENCE, "pdf-front-or-back-matter"
        if asset_id == "WHO_HIV_DAK_2_MAIN":
            if page in {1, 2, 3, 11, 24, 149, 154, 157, 158, 159, 160}:
                return QAQuarantineReason.HEADER_OR_FOOTER, "pdf-cover-or-section-divider"
            if 137 <= page <= 148:
                return (
                    QAQuarantineReason.NON_EVIDENCE,
                    "software-requirement-not-clinical-evidence",
                )
            if page in {4, 5, 6, 7, 8, 9, 10, 150, 151, 152, 153}:
                return QAQuarantineReason.NON_EVIDENCE, "pdf-front-or-back-matter"
    return None


def _base_roles(
    asset_id: str, source_unit_id: str, text: str = ""
) -> tuple[set[EvidenceRole], str]:
    """Roles for one evidence record.

    The DAK branch reads the *asset*, which is what F2 measured as wrong on 91% of the
    records it labelled - but it is committed to signed history and cannot move. The
    narrative branch reads the *passage*, because a narrative asset carries no
    asset-level signal to abuse: one document is the whole corpus contribution.
    """

    if _NARRATIVE_UNIT.match(source_unit_id):
        return _narrative_roles(text)
    if asset_id.endswith("ANNEX_A"):
        return {EvidenceRole.PRIMARY_SUPPORT}, "who-data-dictionary"
    if asset_id.endswith("ANNEX_B"):
        return {EvidenceRole.PRIMARY_SUPPORT}, "who-decision-support"
    if asset_id.endswith("ANNEX_C"):
        return {EvidenceRole.MONITORING}, "who-indicator-definition"
    if asset_id.endswith("MAIN"):
        pdf = _PDF_UNIT.match(source_unit_id)
        page = int(pdf.group("page")) if pdf else 0
        if 12 <= page <= 23:
            return {EvidenceRole.RATIONALE}, "who-dak-methodology"
        if 27 <= page <= 31:
            return {EvidenceRole.APPLICABILITY}, "who-persona-and-scenario"
        if 124 <= page <= 136:
            return {EvidenceRole.MONITORING}, "who-indicator-narrative"
        return {EvidenceRole.PRIMARY_SUPPORT}, "who-controlling-narrative"
    return set(), "unknown-source-classification"


def _narrative_roles(text: str) -> tuple[set[EvidenceRole], str]:
    """Decide from the passage's shape and its deontic register, never from its meaning.

    Errs toward labelling, and the asymmetry is deliberate: a missed recommendation is a
    coverage hole that tells a reader the corpus has nothing, while a wrongly labelled
    methods paragraph is a retrieval problem the answer lane already handles - a claim
    whose cited passages do not support it is discarded whole. The role gate proves
    completeness of kind, never of subject.
    """

    body = text.strip()
    if _NARRATIVE_NON_EVIDENCE.match(body) or _REFERENCE_ENTRY.match(body):
        return set(), "narrative-structural-non-evidence"
    if len(body) < _MIN_STATEMENT_CHARS:
        return set(), "narrative-below-statement-length"
    if _DEONTIC.search(body):
        return {EvidenceRole.PRIMARY_SUPPORT}, "narrative-deontic-statement"
    # Prose that carries no obligation is context: it can support applicability, dosing or
    # monitoring claims alongside a recommendation, but it cannot be the recommendation.
    return {EvidenceRole.RATIONALE}, "narrative-supporting-prose"


def _narrative_applicability(text: str) -> ApplicabilityScope:
    """Applicability read from the passage rather than from a fixed disease vocabulary."""

    population = {
        match.group(0).strip().lower() for match in _NARRATIVE_POPULATION.finditer(text)
    }
    settings = {match.group(0).strip().lower() for match in _NARRATIVE_SETTING.finditer(text)}
    exclusion: set[str] = set()
    for match in re.finditer(
        r"\b(?:contraindicated (?:for|in)|should not be (?:used|offered|given)|"
        r"is not recommended (?:for|in)?|not eligible (?:for|if)?)\s+(.{1,140}?)(?=[.;]|$)",
        text,
        re.IGNORECASE,
    ):
        exclusion.add(match.group(0).strip())
    return ApplicabilityScope(
        population=tuple(sorted(population)),
        care_settings=tuple(sorted(settings)),
        inclusion_criteria=(),
        exclusion_criteria=tuple(sorted(exclusion)),
    )


def _applicability(text: str) -> ApplicabilityScope:
    population = {
        label for pattern, label in _POPULATION_PATTERNS if re.search(pattern, text, re.IGNORECASE)
    }
    care_settings = {
        label
        for pattern, label in _CARE_SETTING_PATTERNS
        if re.search(pattern, text, re.IGNORECASE)
    }
    inclusion: set[str] = set()
    exclusion: set[str] = set()
    for match in re.finditer(
        r"\brequired if\s+(.{1,180}?)(?=(?:\s{2,}|\bnot classifiable\b|$))",
        text,
        re.IGNORECASE,
    ):
        inclusion.add(f"Required if {match.group(1).strip()}")
    for match in re.finditer(
        r"\b(?:contraindicated (?:for|in)|not eligible (?:for|if)?)\s+(.{1,140}?)(?=[.;]|$)",
        text,
        re.IGNORECASE,
    ):
        exclusion.add(match.group(0).strip())
    return ApplicabilityScope(
        population=tuple(population),
        care_settings=tuple(care_settings),
        inclusion_criteria=tuple(inclusion),
        exclusion_criteria=tuple(exclusion),
    )


def _recommendation_grade(text: str) -> RecommendationGrade | None:
    strengths = {
        match.group(1).casefold()
        for match in re.finditer(r"\b(strong|conditional) recommendation\b", text, re.IGNORECASE)
    }
    certainties = {
        match.group(1).casefold()
        for match in re.finditer(
            r"\b(very low|low|moderate|high)[- ]certainty evidence\b", text, re.IGNORECASE
        )
    }
    if len(strengths) > 1 or len(certainties) > 1 or not (strengths or certainties):
        return None
    strength = next(iter(strengths), None)
    certainty = next(iter(certainties), None)
    values = [value for value in (strength, certainty) if value]
    return RecommendationGrade(
        grading_system="WHO GRADE",
        publisher_value="; ".join(values),
        normalized_strength=strength,
        certainty=certainty,
    )


def _quarantine(
    item: QAClassificationInputItem,
    reasons: list[QAQuarantineReason],
    rules: list[str],
) -> _Proposal:
    decision = EvidenceQADecision(
        evidence_id=item.evidence_id,
        materialized_evidence_sha256=item.materialized_evidence_sha256,
        disposition=QADisposition.QUARANTINE,
        quarantine_reasons=tuple(reasons),
        notes=f"Deterministic classification basis: {', '.join(sorted(set(rules)))}.",
    )
    return _Proposal(decision, tuple(rules))


def _apply_exact_deduplication(
    proposals: dict[str, _Proposal],
    materialized: dict[str, MaterializedEvidenceRecord],
) -> None:
    groups: dict[str, list[str]] = defaultdict(list)
    for evidence_id, record in materialized.items():
        groups[record.content.content_search.casefold()].append(evidence_id)
    for evidence_ids in groups.values():
        if len(evidence_ids) < 2:
            continue
        ordered = sorted(
            evidence_ids,
            key=lambda item: _duplicate_priority(item, proposals, materialized),
        )
        for evidence_id in ordered[1:]:
            proposal = proposals[evidence_id]
            reasons = list(proposal.decision.quarantine_reasons)
            if QAQuarantineReason.DUPLICATE not in reasons:
                reasons.append(QAQuarantineReason.DUPLICATE)
            rules = [*proposal.rules, "duplicate-exact-search-content"]
            work_item = QAClassificationInputItem(
                evidence_id=evidence_id,
                materialized_evidence_sha256=materialized[evidence_id].evidence_sha256,
                asset_id=materialized[evidence_id].content.asset_id,
                source_unit_id=materialized[evidence_id].content.source_unit_id,
                replay_passed=(
                    proposal.decision.disposition is QADisposition.APPROVE
                    or QAQuarantineReason.ANCHOR_REPLAY_FAILED not in reasons
                ),
            )
            proposals[evidence_id] = _quarantine(
                work_item,
                reasons,
                rules,
            )


def _duplicate_priority(
    evidence_id: str,
    proposals: dict[str, _Proposal],
    materialized: dict[str, MaterializedEvidenceRecord],
) -> tuple[object, ...]:
    proposal = proposals[evidence_id]
    content = materialized[evidence_id].content
    approved = proposal.decision.disposition is QADisposition.APPROVE
    asset_priority = {
        "WHO_HIV_DAK_2_MAIN": 0,
        "WHO_HIV_DAK_2_ANNEX_B": 1,
        "WHO_HIV_DAK_2_ANNEX_C": 2,
        "WHO_HIV_DAK_2_ANNEX_A": 3,
        "WHO_HIV_DAK_2_ANNEX_D": 4,
    }.get(content.asset_id, 9)
    return (not approved, asset_priority, content.source_unit_id.casefold(), evidence_id)


def _validate_final_decisions(
    items: dict[str, QAClassificationInputItem],
    materialized: dict[str, MaterializedEvidenceRecord],
    decisions: dict[str, EvidenceQADecision],
) -> None:
    approved_content: dict[str, str] = {}
    for evidence_id, decision in decisions.items():
        item = items[evidence_id]
        if decision.disposition is QADisposition.APPROVE and not item.replay_passed:
            raise ValueError(f"failed anchor replay cannot be approved: {evidence_id}")
        if (
            decision.disposition is QADisposition.QUARANTINE
            and not item.replay_passed
            and not {
                QAQuarantineReason.ANCHOR_REPLAY_FAILED,
                QAQuarantineReason.EXTRACTION_STRUCTURE_FAILED,
            }
            & set(decision.quarantine_reasons)
        ):
            raise ValueError(f"failed replay quarantine lacks a replay reason: {evidence_id}")
        if decision.disposition is not QADisposition.APPROVE:
            continue
        normalized = materialized[evidence_id].content.content_search.casefold()
        duplicate = approved_content.get(normalized)
        if duplicate is not None:
            raise ValueError(
                f"duplicate exact search content approved twice: {duplicate}, {evidence_id}"
            )
        approved_content[normalized] = evidence_id
