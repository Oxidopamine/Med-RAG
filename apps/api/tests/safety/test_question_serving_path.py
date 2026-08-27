"""QuestionService driving the real serving path.

These tests wire the real `ReleaseServingPipeline`, `ServingRetrievalService`, and
`GroundedAnswerComposer` together and fake only the four things that are genuinely
external: Qdrant, the embedding runtime, the generation model, and the canonical
evidence repository. Everything the safety argument rests on - the role gate, the
grounding check, the order the phases run in - is the production code path.

The generation backend is unavailable in most of these tests on purpose. That is the
deployment state this lands in: credentials are not present, and the requirement is
that every phase before generation really runs and the path then fails closed into
GENERATION_UNAVAILABLE rather than into a stub abstention that says nothing happened.
"""

from __future__ import annotations

import asyncio

import pytest

from app.corpus_steward.qdrant_index import stable_qdrant_point_id
from app.reasoning.answer_service import GroundedAnswerComposer
from app.reasoning.generation_adapters import GenerationUnavailableError
from app.reasoning.generation_schemas import ModelAnswer
from app.reasoning.presentation import PassagePresenter
from app.reasoning.question_service import QuestionService
from app.reasoning.retrieval_service import ServingRetrievalService
from app.reasoning.serving_pipeline import GLOBAL_JURISDICTION, ReleaseServingPipeline
from app.schemas.corpus import (
    ActiveCorpusRelease,
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
from app.schemas.domain import ClinicalContext, SourceStatus, utc_now
from app.schemas.questions import (
    TERMINAL_STATUSES,
    EvidenceDetail,
    EvidenceLocator,
    QuestionCreate,
    QuestionStatus,
    SourceFilters,
)

RELEASE_ID = "CR_TEST"
COLLECTION = "corpus_CR_TEST--vp-test"
ANNEX_B = "WHO_HIV_DAK_2:2:WHO_HIV_DAK_2_ANNEX_B"
ANNEX_A = "WHO_HIV_DAK_2:2:WHO_HIV_DAK_2_ANNEX_A"
DECISION_TABLE = "HIV.D21.2.DT Drug Interactions"
DIGEST = "a" * 64

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


def decision_rule_row(rule_id: str, drug: str, guidance: str, row: int) -> str:
    return "\n".join(
        (
            f"B{row}={rule_id}",
            f"C{row}=\"ART regimen composition\" IN 'DTG'",
            f"D{row}=\"Medication/drug\"='{drug}'",
            f"E{row}=\"Age\" >= 10 years",
            f"F{row}=PlanDefinition",
            f"G{row}=Set \"Dose adjustment recommended\"=True",
            f"H{row}={guidance}",
            f"I{row}=Consolidated guidelines, 2021, Table 4.14",
        )
    )


DATA_DICTIONARY_ROW = "\n".join(
    (
        "A145=HIV.D8 Capture or update client history",
        "B145=HIV.D.DE144",
        "C145=DTG",
        "D145=Treated with dolutegravir (DTG)",
        "E145=Input Option",
        "F145=Codes",
        "G145=DTG",
    )
)


def evidence(
    evidence_id: str,
    content_exact: str,
    *,
    source_version_id: str = ANNEX_B,
    table_id: str = DECISION_TABLE,
    row_index: int = 7,
    roles: tuple[EvidenceRole, ...] = (
        EvidenceRole.PRIMARY_SUPPORT,
        EvidenceRole.APPLICABILITY,
    ),
    jurisdiction: str = GLOBAL_JURISDICTION,
) -> CorpusEvidenceRecord:
    return CorpusEvidenceRecord(
        corpus_release_id=RELEASE_ID,
        evidence_id=evidence_id,
        source_id="SRC_TEST",
        source_version_id=source_version_id,
        source_artifact_sha256=DIGEST,
        publisher_id="WHO",
        jurisdiction=jurisdiction,
        language="en",
        lifecycle_status=SourceStatus.EFFECTIVE,
        evidence_roles=roles,
        content_exact=content_exact,
        content_search=" ".join(content_exact.split()),
        anchors=tuple(
            SourceAnchor(
                kind=LocatorKind.TABLE_CELL,
                source_uri="https://example.invalid/annex.xlsx",
                table_id=table_id,
                row_index=row_index,
                column_index=index,
            )
            for index in range(2)
        ),
        render_allowed=True,
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


HEADER = evidence("EV_HEADER", DECISION_HEADER_ROW, row_index=6)
RIFAMPICIN = evidence(
    "EV_RIF",
    decision_rule_row("HIV.D21.2.DT.01", "Rifampicin", "Double the daily dose of DTG.", 8),
    row_index=7,
)
METFORMIN = evidence(
    "EV_MET",
    decision_rule_row("HIV.D21.2.DT.48", "Metformin", "Avoid high-dose metformin.", 9),
    row_index=8,
)
CODE_LIST_ENTRY = evidence(
    "EV_CODE",
    DATA_DICTIONARY_ROW,
    source_version_id=ANNEX_A,
    table_id="HIV.D Care-Treatment",
    row_index=144,
)


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
    """Returns one fixed ranking per lane, honouring the filter it is handed.

    The filter is applied rather than ignored so a jurisdiction or lifecycle mistake in
    the service under test shows up here as an empty ranking, which is what it would do
    against the real index.
    """

    def __init__(self, records, order, *, fail_lanes: frozenset[str] = frozenset()) -> None:
        self._records = records
        self._order = order
        self._fail_lanes = fail_lanes
        self.filters: list[dict] = []

    async def query_points(self, collection, request):
        using = request["using"]
        if using in self._fail_lanes:
            raise RuntimeError(f"{using} lane is down")
        query_filter = request["filter"]
        self.filters.append(query_filter)
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
            for evidence_id, record in (
                (key, self._records[key]) for key in self._order
            )
            if _matches(query_filter, record)
        ]


def _matches(query_filter: dict, record: CorpusEvidenceRecord) -> bool:
    for condition in query_filter["must"]:
        key, match = condition["key"], condition["match"]
        value = {
            "corpus_release_id": record.corpus_release_id,
            "approval_status": "APPROVED",
            "lifecycle_status": record.lifecycle_status.value,
            "jurisdiction": record.jurisdiction,
            "language": record.language,
        }[key]
        if "value" in match and value != match["value"]:
            return False
        if "any" in match and value not in match["any"]:
            return False
    return True


class _UnavailableBackend:
    """What the real Vertex adapter does with no credentials present."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, system_prompt: str, user_content: str) -> ModelAnswer:
        self.calls += 1
        raise GenerationUnavailableError(
            "google.auth.exceptions.DefaultCredentialsError: could not automatically "
            "determine credentials"
        )


class _StubBackend:
    def __init__(self, answer: ModelAnswer) -> None:
        self._answer = answer
        self.calls = 0
        self.user_content: str | None = None

    async def generate(self, *, system_prompt: str, user_content: str) -> ModelAnswer:
        self.calls += 1
        self.user_content = user_content
        return self._answer


class _DetailRepository:
    """Stands in for `SQLCorpusReleaseRepository.evidence_details`.

    Like the real one, it omits what it cannot safely resolve rather than raising.
    """

    def __init__(self, *, unresolvable: frozenset[str] = frozenset()) -> None:
        self._unresolvable = unresolvable
        self.requested: set[str] = set()

    async def evidence_details(self, corpus_release_id, evidence_ids):
        self.requested |= set(evidence_ids)
        return [
            EvidenceDetail(
                evidence_id=evidence_id,
                exact_text="approved content",
                evidence_roles=["PRIMARY_SUPPORT"],
                source_id="SRC_TEST",
                source_version_id=ANNEX_B,
                source_title="WHO SMART HIV DAK",
                source_version_label="2.0",
                publisher_name="World Health Organization",
                source_url="https://example.invalid/dak",
                source_class="GUIDELINE",
                jurisdiction=GLOBAL_JURISDICTION,
                language="en",
                lifecycle_status="EFFECTIVE",
                render_allowed=True,
                locators=[
                    EvidenceLocator(
                        kind=LocatorKind.TABLE_CELL.value,
                        source_uri="https://example.invalid/annex.xlsx",
                    )
                ],
            )
            for evidence_id in sorted(set(evidence_ids))
            if evidence_id not in self._unresolvable
        ]


def active_release() -> ActiveCorpusRelease:
    return ActiveCorpusRelease(
        corpus_release_id=RELEASE_ID,
        manifest_sha256=DIGEST,
        qdrant_collection=COLLECTION,
        activated_at=utc_now(),
    )


def build_service(
    *,
    records: list[CorpusEvidenceRecord],
    backend,
    details: _DetailRepository | None = None,
    qdrant: _Qdrant | None = None,
    top_k: int = 10,
    release: ActiveCorpusRelease | None = None,
    ranked: list[str] | None = None,
) -> tuple[QuestionService, _Qdrant, _DetailRepository]:
    # `records` is the release the presenter reads column labels from; `ranked` is what
    # the index returns. A decision table's header row belongs in the first and not
    # necessarily in the second.
    evidence_by_id = {record.evidence_id: record for record in records}
    ordering = ranked if ranked is not None else [record.evidence_id for record in records]
    fake_qdrant = qdrant or _Qdrant(evidence_by_id, ordering)
    detail_repository = details or _DetailRepository()
    pipeline = ReleaseServingPipeline(
        corpus_release_id=RELEASE_ID,
        qdrant_collection=COLLECTION,
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        evidence=evidence_by_id,
        retrieval=ServingRetrievalService(
            fake_qdrant,
            _EmbeddingBackend(),
            presenter=PassagePresenter.for_release(records),
            top_k=top_k,
        ),
        composer=GroundedAnswerComposer(backend),
        evidence_details_provider=detail_repository,
    )
    pinned = release or active_release()

    async def provider() -> ActiveCorpusRelease:
        return pinned

    service = QuestionService(active_release_provider=provider, pipeline=pipeline)
    return service, fake_qdrant, detail_repository


async def run_to_terminal(service: QuestionService, question_id: str):
    for _ in range(200):
        result = service.result(question_id)
        if result.status in TERMINAL_STATUSES:
            return result
        await asyncio.sleep(0.01)
    raise AssertionError(f"question did not reach a terminal status: {result.status}")


async def run(service: QuestionService, question: str, **kwargs):
    accepted = await service.submit(QuestionCreate(question=question, **kwargs))
    return await run_to_terminal(service, accepted.question_id)


async def test_missing_generation_credentials_abstain_after_real_retrieval() -> None:
    """The deployment state this lands in: everything runs, generation fails closed."""

    backend = _UnavailableBackend()
    service, qdrant, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN], backend=backend
    )

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.reason_code == "GENERATION_UNAVAILABLE"
    # The failure is reported, not paraphrased into something reassuring.
    assert "DefaultCredentialsError" in result.abstention.message
    # Retrieval really ran: the model was reached, and the abstention names what was
    # found rather than claiming nothing was.
    assert backend.calls == 1
    assert qdrant.filters, "retrieval never queried the index"
    assert result.abstention.closest_evidence_ids
    # An abstention never renders claims or exposes evidence.
    assert result.claims == []
    assert result.evidence_details == []
    assert result.retrieval_candidates == []
    await service.close()


async def test_incomplete_evidence_role_set_abstains_before_any_model_call() -> None:
    backend = _UnavailableBackend()
    # A code-list entry declares PRIMARY_SUPPORT, but a data-dictionary entry cannot
    # carry a recommendation, so the role gate is not satisfied by it.
    service, _, details = build_service(records=[CODE_LIST_ENTRY], backend=backend)

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.reason_code == "INCOMPLETE_EVIDENCE_ROLE_SET"
    assert result.abstention.missing_evidence_roles == ["PRIMARY_SUPPORT"]
    assert result.abstention.closest_evidence_ids == ["EV_CODE"]
    # The point of the gate: no model call. That is the invariant - nothing is generated,
    # so nothing unsupported can be rendered.
    assert backend.calls == 0

    # Canonical detail IS fetched, for the near misses only, so the reader can open what
    # came closest and judge whether the corpus is thin or the question was wrong. It is a
    # read of records retrieval already returned; it renders no claim and cites nothing.
    assert details.requested == {"EV_CODE"}
    assert [detail.evidence_id for detail in result.abstention.closest_evidence] == [
        "EV_CODE"
    ]
    await service.close()


async def test_empty_retrieval_abstains_without_reaching_the_model() -> None:
    backend = _UnavailableBackend()
    # The only record is scoped to a jurisdiction the client did not ask for, and it is
    # not global, so widening to WORLD does not reach it either.
    us_only = evidence(
        "EV_US",
        decision_rule_row("HIV.D21.2.DT.01", "Rifampicin", "Double the dose.", 8),
        jurisdiction="US",
    )
    service, _, _ = build_service(records=[HEADER, us_only], backend=backend,
                                 ranked=["EV_US"])

    result = await run(
        service,
        "What are the contraindications to dolutegravir?",
        source_filters=SourceFilters(jurisdictions=["JP"]),
    )

    assert result.status is QuestionStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.reason_code == "NO_EVIDENCE_RETRIEVED"
    assert result.abstention.closest_evidence_ids == []
    assert backend.calls == 0
    await service.close()


async def test_progress_events_mark_only_phases_that_ran() -> None:
    backend = _UnavailableBackend()
    service, _, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN], backend=backend
    )
    accepted = await service.submit(
        QuestionCreate(question="What are the contraindications to dolutegravir?")
    )

    events = [event async for event in service.events(accepted.question_id)]
    statuses = [event.status for event in events]

    assert statuses == [
        QuestionStatus.QUEUED,
        QuestionStatus.CONTEXT_EXTRACTED,
        QuestionStatus.RETRIEVING,
        QuestionStatus.CHECKING_EVIDENCE_COMPLETENESS,
        QuestionStatus.VERIFYING,
        QuestionStatus.ABSTAINED,
    ]
    # There is no counter-evidence search and no reranking lane in the serving path.
    # Both remain in the contract; neither is claimed to have happened.
    assert QuestionStatus.SEARCHING_COUNTER_EVIDENCE not in statuses
    assert QuestionStatus.RERANKING not in statuses
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    await service.close()


async def test_gate_abstention_stops_the_event_stream_before_verifying() -> None:
    service, _, _ = build_service(records=[CODE_LIST_ENTRY], backend=_UnavailableBackend())
    accepted = await service.submit(
        QuestionCreate(question="What are the contraindications to dolutegravir?")
    )

    statuses = [event.status async for event in service.events(accepted.question_id)]

    # VERIFYING would be a claim that composition was attempted. It was not.
    assert QuestionStatus.VERIFYING not in statuses
    assert statuses[-2] is QuestionStatus.CHECKING_EVIDENCE_COMPLETENESS
    assert statuses[-1] is QuestionStatus.ABSTAINED
    await service.close()


async def test_grounded_answer_reaches_answer_ready_with_canonical_details() -> None:
    backend = _StubBackend(
        ModelAnswer(
            sufficient_evidence=True,
            claims=[
                {
                    "text": "Double the daily dose of dolutegravir with rifampicin.",
                    "evidence_ids": ["EV_RIF"],
                }
            ],
        )
    )
    service, _, details = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN],
        backend=backend,
        ranked=["EV_RIF", "EV_MET"],
    )

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.ANSWER_READY
    assert result.abstention is None
    assert [claim.evidence_ids for claim in result.claims] == [["EV_RIF"]]
    assert result.verification_summary.supported_claims == 1
    assert result.verification_summary.withheld_claims == 0
    # The uncited passage is offered as a candidate, at the rank it held.
    assert [
        (candidate.evidence_id, candidate.retrieval_rank)
        for candidate in result.retrieval_candidates
    ] == [("EV_MET", 2)]
    # Every referenced ID has a canonical record behind it.
    assert {detail.evidence_id for detail in result.evidence_details} == {
        "EV_RIF",
        "EV_MET",
    }
    # The model saw the presentation view, never the cell-addressed anchor.
    assert backend.user_content is not None
    assert "B8=" not in backend.user_content
    assert "Double the daily dose of DTG." in backend.user_content
    await service.close()


async def test_a_lane_failure_does_not_by_itself_block_an_answer() -> None:
    backend = _StubBackend(
        ModelAnswer(
            sufficient_evidence=True,
            claims=[
                {"text": "Adjust the dose.", "evidence_ids": ["EV_RIF"]},
            ],
        )
    )
    records = [HEADER, RIFAMPICIN, METFORMIN]
    evidence_by_id = {record.evidence_id: record for record in records}
    qdrant = _Qdrant(
        evidence_by_id,
        [record.evidence_id for record in records],
        fail_lanes=frozenset({"dense"}),
    )
    service, _, _ = build_service(records=records, backend=backend, qdrant=qdrant)

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.ANSWER_READY
    assert result.claims
    await service.close()


async def test_a_claim_whose_canonical_record_is_unresolvable_withholds_the_answer() -> None:
    backend = _StubBackend(
        ModelAnswer(
            sufficient_evidence=True,
            claims=[{"text": "Adjust the dose.", "evidence_ids": ["EV_RIF"]}],
        )
    )
    service, _, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN],
        backend=backend,
        details=_DetailRepository(unresolvable=frozenset({"EV_RIF"})),
    )

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.reason_code == "EVIDENCE_DETAIL_UNAVAILABLE"
    assert result.abstention.closest_evidence_ids == ["EV_RIF"]
    assert result.claims == []
    await service.close()


async def test_an_unresolvable_uncited_candidate_is_dropped_not_fatal() -> None:
    backend = _StubBackend(
        ModelAnswer(
            sufficient_evidence=True,
            claims=[{"text": "Adjust the dose.", "evidence_ids": ["EV_RIF"]}],
        )
    )
    service, _, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN],
        backend=backend,
        # EV_MET is offered as a candidate but cannot be resolved. It supported no
        # claim, so withholding the answer over it would help nobody.
        details=_DetailRepository(unresolvable=frozenset({"EV_MET"})),
        ranked=["EV_RIF", "EV_MET"],
    )

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.ANSWER_READY
    assert result.retrieval_candidates == []
    assert {detail.evidence_id for detail in result.evidence_details} == {"EV_RIF"}
    await service.close()


async def test_model_declaring_insufficient_evidence_lowers_the_outcome() -> None:
    backend = _StubBackend(
        ModelAnswer(
            sufficient_evidence=False,
            insufficiency_note="The passages describe drug interactions, not contraindications.",
        )
    )
    service, _, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN], backend=backend
    )

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.reason_code == "MODEL_DECLARED_INSUFFICIENT"
    await service.close()


async def test_a_claim_citing_unretrieved_evidence_does_not_survive() -> None:
    backend = _StubBackend(
        ModelAnswer(
            sufficient_evidence=True,
            claims=[
                {"text": "Fabricated support.", "evidence_ids": ["EV_NOT_RETRIEVED"]}
            ],
        )
    )
    service, _, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN], backend=backend
    )

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.reason_code == "NO_CLAIM_SURVIVED_GROUNDING"
    assert result.claims == []
    await service.close()


async def test_a_default_jurisdiction_filter_still_admits_global_records() -> None:
    """The whole HIV release is `jurisdiction=WORLD`; the client default is US/EU/UK."""

    backend = _UnavailableBackend()
    service, qdrant, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN], backend=backend
    )

    result = await run(
        service,
        "What are the contraindications to dolutegravir?",
        source_filters=SourceFilters(jurisdictions=["US", "EU", "UK"]),
    )

    # It reached generation, which means retrieval and the role gate both passed.
    assert result.abstention is not None
    assert result.abstention.reason_code == "GENERATION_UNAVAILABLE"
    jurisdiction_filters = [
        condition["match"]["any"]
        for query_filter in qdrant.filters
        for condition in query_filter["must"]
        if condition["key"] == "jurisdiction"
    ]
    assert jurisdiction_filters
    for allowed in jurisdiction_filters:
        # The client's own filter is preserved, not replaced.
        assert set(allowed) == {"US", "EU", "UK", GLOBAL_JURISDICTION}
    await service.close()


async def test_lifecycle_filter_is_applied_on_every_lane() -> None:
    backend = _UnavailableBackend()
    service, qdrant, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN], backend=backend
    )

    await run(service, "What are the contraindications to dolutegravir?")

    assert qdrant.filters
    for query_filter in qdrant.filters:
        lifecycle = [
            condition["match"]["any"]
            for condition in query_filter["must"]
            if condition["key"] == "lifecycle_status"
        ]
        assert lifecycle == [["EFFECTIVE"]]
    await service.close()


async def test_a_moved_active_release_pointer_fails_closed() -> None:
    backend = _StubBackend(
        ModelAnswer(
            sufficient_evidence=True,
            claims=[{"text": "Adjust the dose.", "evidence_ids": ["EV_RIF"]}],
        )
    )
    service, _, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN],
        backend=backend,
        release=ActiveCorpusRelease(
            corpus_release_id="CR_SOMETHING_ELSE",
            manifest_sha256=DIGEST,
            qdrant_collection="corpus_CR_SOMETHING_ELSE--vp-test",
            activated_at=utc_now(),
        ),
    )

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.FAILED
    assert result.abstention is not None
    assert result.abstention.reason_code == "PIPELINE_FAILURE"
    assert result.claims == []
    assert backend.calls == 0
    await service.close()


async def test_context_replacement_reruns_the_serving_path_with_the_given_context() -> None:
    backend = _UnavailableBackend()
    service, qdrant, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN], backend=backend
    )
    accepted = await service.submit(
        QuestionCreate(question="What are the contraindications to dolutegravir?")
    )
    await run_to_terminal(service, accepted.question_id)
    calls_after_first = backend.calls
    queries_after_first = len(qdrant.filters)

    supplied = ClinicalContext(age=41, question_type="guideline_lookup")
    replaced = await service.replace_context(accepted.question_id, supplied)
    result = await run_to_terminal(service, replaced.question_id)

    # A new question, run through the same real path rather than short-circuited.
    assert replaced.question_id != accepted.question_id
    assert backend.calls == calls_after_first + 1
    assert len(qdrant.filters) > queries_after_first
    assert result.abstention is not None
    assert result.abstention.reason_code == "GENERATION_UNAVAILABLE"
    # The supplied context is served back verbatim; it is not re-derived from the text.
    assert result.interpreted_context is not None
    assert result.interpreted_context.age == 41
    await service.close()


@pytest.mark.parametrize("top_k", [1, 2])
async def test_depth_changes_which_passages_are_offered_not_whether_it_is_honest(
    top_k: int,
) -> None:
    backend = _UnavailableBackend()
    service, _, _ = build_service(
        records=[HEADER, RIFAMPICIN, METFORMIN],
        backend=backend,
        top_k=top_k,
        ranked=["EV_RIF", "EV_MET"],
    )

    result = await run(service, "What are the contraindications to dolutegravir?")

    assert result.status is QuestionStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.reason_code == "GENERATION_UNAVAILABLE"
    assert len(result.abstention.closest_evidence_ids) <= top_k
    await service.close()
