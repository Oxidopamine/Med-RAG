"""The serving seam: what it answers from, and everything it refuses to answer from."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.corpus.releases import ReleaseEvidenceSet
from app.corpus_steward.candidate_schemas import RetrievalCandidateManifest
from app.corpus_steward.qdrant_index import (
    candidate_qdrant_collection,
    stable_qdrant_point_id,
)
from app.corpus_steward.vector_producer import DeterministicHashingBackend
from app.reasoning.answer_service import GroundedAnswerComposer
from app.reasoning.generation_schemas import ModelAnswer, ModelClaim
from app.reasoning.question_service import QuestionService
from app.retrieval.serving import (
    ServingAbstention,
    ServingConfigurationError,
    ServingRetrievalEngine,
)
from app.schemas.corpus import (
    CorpusReleaseBundle,
    ServingReleaseBinding,
)
from app.schemas.domain import utc_now
from app.schemas.questions import (
    EvidenceDetail,
    EvidenceLocator,
    QuestionCreate,
    QuestionStatus,
    SourceFilters,
)

FIXTURE_PATH = Path(__file__).parents[4] / "data" / "fixtures" / "corpus-release-v1.json"
SYNTHETIC_CANDIDATE_PATH = (
    Path(__file__).parents[4] / "models" / "configs" / "synthetic-candidate-v1.json"
)
PRIMARY = "EV_FIXTURE_PRIMARY_001"
APPLICABILITY = "EV_FIXTURE_APPLICABILITY_001"
EXCEPTION = "EV_FIXTURE_EXCEPTION_001"


def fixture_bundle() -> CorpusReleaseBundle:
    return CorpusReleaseBundle.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))


def fixture_candidate() -> RetrievalCandidateManifest:
    return RetrievalCandidateManifest.model_validate_json(
        SYNTHETIC_CANDIDATE_PATH.read_text(encoding="utf-8")
    )


def binding_for(
    bundle: CorpusReleaseBundle,
    candidate: RetrievalCandidateManifest,
    *,
    collection: str | None = None,
    candidate_sha256: str | None = None,
    valid_for: timedelta = timedelta(days=30),
) -> ServingReleaseBinding:
    return ServingReleaseBinding(
        corpus_release_id=bundle.manifest.content.corpus_release_id,
        manifest_sha256=bundle.manifest.manifest_sha256,
        qdrant_collection=collection or candidate_qdrant_collection(bundle, candidate),
        candidate_configuration_sha256=candidate_sha256 or candidate.candidate_sha256,
        vector_batch_sha256="b" * 64,
        index_attestation_sha256="c" * 64,
        benchmark_acceptance_sha256="d" * 64,
        benchmark_valid_until=utc_now() + valid_for,
        activated_at=utc_now(),
        manifest=bundle.manifest,
    )


class FakeReleaseReader:
    """A release the serving engine can resolve, with controllable detail resolution."""

    def __init__(
        self,
        bundle: CorpusReleaseBundle,
        binding: ServingReleaseBinding | None,
        *,
        restricted: frozenset[str] = frozenset(),
        unresolvable: frozenset[str] = frozenset(),
    ) -> None:
        self._bundle = bundle
        self._binding = binding
        self._restricted = restricted
        self._unresolvable = unresolvable
        self.detail_requests: list[list[str]] = []

    async def active_serving_binding(self) -> ServingReleaseBinding | None:
        return self._binding

    async def release_evidence(self, corpus_release_id: str) -> ReleaseEvidenceSet:
        return ReleaseEvidenceSet(
            corpus_release_id=corpus_release_id,
            records={item.evidence_id: item for item in self._bundle.evidence},
            publisher_names={"PUB_FIXTURE": "Fixture Guideline Body"},
        )

    async def evidence_details(
        self, corpus_release_id: str, evidence_ids
    ) -> list[EvidenceDetail]:
        self.detail_requests.append(list(evidence_ids))
        wanted = set(evidence_ids) - self._unresolvable
        details = []
        for record in self._bundle.evidence:
            if record.evidence_id not in wanted:
                continue
            render_allowed = record.evidence_id not in self._restricted
            details.append(
                EvidenceDetail(
                    evidence_id=record.evidence_id,
                    exact_text=record.content_exact if render_allowed else None,
                    evidence_roles=[role.value for role in record.evidence_roles],
                    source_id=record.source_id,
                    source_version_id=record.source_version_id,
                    source_title="Synthetic guideline",
                    source_version_label="2026",
                    publisher_name="Fixture Guideline Body",
                    source_url="https://fixtures.invalid/synthetic-guideline.pdf",
                    source_class="E1",
                    jurisdiction=record.jurisdiction,
                    language=record.language,
                    lifecycle_status=record.lifecycle_status.value,
                    render_allowed=render_allowed,
                    locators=[
                        EvidenceLocator(
                            kind="PDF",
                            source_uri="https://fixtures.invalid/synthetic-guideline.pdf",
                            pdf_page=1,
                            bbox=(72, 72, 500, 90),
                            exact_highlight_available=render_allowed,
                        )
                    ],
                )
            )
        return sorted(details, key=lambda item: item.evidence_id)


class FakeServingQdrant:
    """Returns a fixed ranking per vector name, with optional payload tampering."""

    def __init__(
        self,
        bundle: CorpusReleaseBundle,
        rankings: dict[str, list[str]],
        *,
        failing_vectors: frozenset[str] = frozenset(),
        payload_overrides: dict[str, dict[str, Any]] | None = None,
        honour_filters: bool = True,
    ) -> None:
        self._evidence = {item.evidence_id: item for item in bundle.evidence}
        self._release_id = bundle.manifest.content.corpus_release_id
        self._rankings = rankings
        self._failing = failing_vectors
        self._overrides = payload_overrides or {}
        self._honour_filters = honour_filters
        self.requests: list[dict[str, Any]] = []

    async def query_points(
        self, collection: str, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        self.requests.append(request)
        if request["using"] in self._failing:
            raise RuntimeError("qdrant lane unavailable")
        ranking = self._rankings[request["using"]][: request["limit"]]
        results = []
        for rank, evidence_id in enumerate(ranking, start=1):
            record = self._evidence[evidence_id]
            payload = {
                "approval_status": "APPROVED",
                "corpus_release_id": self._release_id,
                "evidence_id": evidence_id,
                "evidence_roles": [item.value for item in record.evidence_roles],
                "evidence_sha256": record.sha256,
                "jurisdiction": record.jurisdiction,
                "language": record.language,
                "publisher_id": record.publisher_id,
                "source_class": "E1",
                "source_version_id": record.source_version_id,
                "lifecycle_status": record.lifecycle_status.value,
            }
            payload.update(self._overrides.get(evidence_id, {}))
            if self._honour_filters and not _payload_matches(payload, request["filter"]):
                continue
            results.append(
                {
                    "id": stable_qdrant_point_id(evidence_id),
                    "payload": payload,
                    "score": 1.0 / rank,
                }
            )
        return results


def _payload_matches(payload: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Apply the filter server-side, the way Qdrant would."""

    for term in filters["must"]:
        match = term["match"]
        value = payload.get(term["key"])
        if "value" in match and value != match["value"]:
            return False
        if "any" in match and value not in match["any"]:
            return False
    return True


def build_engine(
    reader: FakeReleaseReader,
    qdrant: FakeServingQdrant,
    candidate: RetrievalCandidateManifest | None = None,
) -> ServingRetrievalEngine:
    candidate = candidate or fixture_candidate()
    return ServingRetrievalEngine(
        qdrant, deterministic_backend(), candidate, reader
    )


def deterministic_backend() -> DeterministicHashingBackend:
    """The backend the sealed synthetic candidate pins, by artifact digest.

    The dimensions are not decorative: they are what the candidate lane digests were
    computed over, so a different pair produces a different artifact identity and the
    engine refuses to start.
    """

    return DeterministicHashingBackend(dense_dimension=32, sparse_dimension=2**8)


def default_rankings() -> dict[str, list[str]]:
    return {
        "dense": [PRIMARY, APPLICABILITY, EXCEPTION],
        "sparse": [PRIMARY, EXCEPTION, APPLICABILITY],
    }


async def test_serving_searches_the_accepted_collection_under_a_release_filter() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve("synthetic guideline question", SourceFilters(jurisdictions=[]))

    assert result.retrieved
    assert [item.evidence_id for item in result.ranked][0] == PRIMARY
    assert [item.rank for item in result.ranked] == list(range(1, len(result.ranked) + 1))
    assert {item.evidence_id for item in result.passages} == {
        PRIMARY,
        APPLICABILITY,
        EXCEPTION,
    }
    expected_collection = candidate_qdrant_collection(bundle, candidate)
    assert all(request["using"] in {"dense", "sparse"} for request in qdrant.requests)
    for request in qdrant.requests:
        must = request["filter"]["must"]
        assert {
            "key": "corpus_release_id",
            "match": {"value": bundle.manifest.content.corpus_release_id},
        } in must
        assert {"key": "approval_status", "match": {"value": "APPROVED"}} in must
    assert engine.binding_mismatch(binding_for(bundle, candidate)) is None
    assert expected_collection.startswith("corpus_CR_FIXTURE_2026_08_25")


async def test_a_request_filter_reaches_qdrant_rather_than_being_dropped() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve(
        "synthetic guideline question",
        SourceFilters(jurisdictions=["TEST"], organizations=["Fixture Guideline Body"]),
    )

    assert result.retrieved
    must = qdrant.requests[0]["filter"]["must"]
    assert {"key": "jurisdiction", "match": {"any": ["TEST"]}} in must
    assert {"key": "publisher_id", "match": {"any": ["PUB_FIXTURE"]}} in must


async def test_an_unresolvable_organization_abstains_instead_of_widening() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve(
        "synthetic guideline question",
        SourceFilters(jurisdictions=[], organizations=["Not A Publisher"]),
    )

    assert result.abstention is ServingAbstention.UNKNOWN_SOURCE_ORGANIZATION
    assert not qdrant.requests


async def test_a_candidate_the_release_never_accepted_cannot_serve() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(
        bundle, binding_for(bundle, candidate, candidate_sha256="e" * 64)
    )
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve("synthetic guideline question", SourceFilters(jurisdictions=[]))

    assert result.abstention is ServingAbstention.SERVING_BINDING_MISMATCH
    assert not qdrant.requests


async def test_a_collection_the_candidate_would_not_have_built_cannot_serve() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(
        bundle, binding_for(bundle, candidate, collection="corpus_CR_FIXTURE--vp-000000")
    )
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve("synthetic guideline question", SourceFilters(jurisdictions=[]))

    assert result.abstention is ServingAbstention.SERVING_BINDING_MISMATCH
    assert "collection" in (result.abstention_message or "")
    assert not qdrant.requests


async def test_an_expired_benchmark_acceptance_stops_serving() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(
        bundle, binding_for(bundle, candidate, valid_for=timedelta(seconds=-1))
    )
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve("synthetic guideline question", SourceFilters(jurisdictions=[]))

    assert result.abstention is ServingAbstention.BENCHMARK_ACCEPTANCE_STALE
    assert not qdrant.requests


async def test_no_active_release_abstains_before_any_search() -> None:
    bundle = fixture_bundle()
    reader = FakeReleaseReader(bundle, None)
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant)

    result = await engine.retrieve("synthetic guideline question", SourceFilters(jurisdictions=[]))

    assert result.abstention is ServingAbstention.NO_ACTIVE_RELEASE
    assert not qdrant.requests


async def test_a_failed_lane_abstains_rather_than_answering_from_a_partial_fusion() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(
        bundle, default_rankings(), failing_vectors=frozenset({"dense"})
    )
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve("synthetic guideline question", SourceFilters(jurisdictions=[]))

    assert result.abstention is ServingAbstention.RETRIEVAL_LANE_FAILURE
    assert result.lane_failures == ("DENSE_LANE_FAILURE:RuntimeError",)
    assert not result.passages


async def test_a_result_that_escapes_the_request_filter_fails_closed() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(
        bundle,
        default_rankings(),
        payload_overrides={PRIMARY: {"jurisdiction": "ELSEWHERE"}},
        honour_filters=False,
    )
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve(
        "synthetic guideline question", SourceFilters(jurisdictions=["TEST"])
    )

    assert result.abstention is ServingAbstention.RETRIEVAL_LANE_FAILURE
    assert any("LANE_FAILURE:RetrievalError" in item for item in result.lane_failures)


async def test_a_payload_that_disagrees_with_the_release_fails_closed() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(
        bundle,
        default_rankings(),
        payload_overrides={PRIMARY: {"evidence_sha256": "f" * 64}},
    )
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve("synthetic guideline question", SourceFilters(jurisdictions=[]))

    assert result.abstention is ServingAbstention.RETRIEVAL_LANE_FAILURE


async def test_restricted_evidence_ranks_but_its_text_never_leaves_the_process() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(
        bundle, binding_for(bundle, candidate), restricted=frozenset({PRIMARY})
    )
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve("synthetic guideline question", SourceFilters(jurisdictions=[]))

    assert result.retrieved
    assert PRIMARY in {item.evidence_id for item in result.ranked}
    assert result.withheld_restricted_evidence_ids == (PRIMARY,)
    assert PRIMARY not in {item.evidence_id for item in result.passages}
    assert all(item.text for item in result.passages)


async def test_evidence_that_cannot_be_resolved_to_a_record_abstains() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(
        bundle, binding_for(bundle, candidate), unresolvable=frozenset({PRIMARY})
    )
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve("synthetic guideline question", SourceFilters(jurisdictions=[]))

    assert result.abstention is ServingAbstention.EVIDENCE_DETAILS_UNAVAILABLE
    assert not result.passages


async def test_a_backend_that_is_not_the_pinned_one_cannot_build_an_engine() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(bundle, default_rankings())

    with pytest.raises(ServingConfigurationError, match="dense model artifact"):
        ServingRetrievalEngine(
            qdrant,
            DeterministicHashingBackend(dense_dimension=256, sparse_dimension=2**8),
            candidate,
            reader,
        )


class StubGeneration:
    """A generation backend that cites exactly what it was given."""

    def __init__(self, evidence_ids: tuple[str, ...]) -> None:
        self._evidence_ids = evidence_ids
        self.calls: list[str] = []

    async def generate(self, *, system_prompt: str, user_content: str) -> ModelAnswer:
        self.calls.append(user_content)
        return ModelAnswer(
            sufficient_evidence=True,
            claims=[
                ModelClaim(
                    text="The synthetic recommendation applies in the outpatient setting.",
                    evidence_ids=list(self._evidence_ids),
                )
            ],
        )


async def test_the_question_path_reports_only_the_stages_that_ran() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)
    generation = StubGeneration((PRIMARY,))
    service = QuestionService(
        active_release_provider=lambda: _release(bundle, candidate),
        retrieval_engine=engine,
        answer_composer=GroundedAnswerComposer(generation),
    )

    accepted = await service.submit(
        QuestionCreate(
            question="A synthetic guideline question",
            source_filters=SourceFilters(jurisdictions=["TEST"]),
        )
    )
    events = [event async for event in service.events(accepted.question_id)]
    statuses = [event.status for event in events]
    result = service.result(accepted.question_id)

    assert statuses[0] is QuestionStatus.QUEUED
    assert statuses[-1] is QuestionStatus.ANSWER_READY
    assert QuestionStatus.RETRIEVING in statuses
    # The sealed synthetic candidate configures no reranker and no query expansion,
    # so neither stage is reported. A timer would have reported both.
    assert QuestionStatus.RERANKING not in statuses
    assert QuestionStatus.SEARCHING_COUNTER_EVIDENCE not in statuses
    assert result.status is QuestionStatus.ANSWER_READY
    assert [claim.evidence_ids for claim in result.claims] == [[PRIMARY]]
    assert [detail.evidence_id for detail in result.evidence_details] == [PRIMARY]
    assert result.corpus_release is not None
    assert result.corpus_release.corpus_release_id == bundle.manifest.content.corpus_release_id
    assert generation.calls and PRIMARY in generation.calls[0]
    await service.close()


async def test_the_question_path_abstains_with_the_retrieval_reason() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, None)
    qdrant = FakeServingQdrant(bundle, default_rankings())
    service = QuestionService(
        active_release_provider=lambda: _release(bundle, candidate),
        retrieval_engine=build_engine(reader, qdrant, candidate),
        answer_composer=GroundedAnswerComposer(StubGeneration((PRIMARY,))),
    )

    accepted = await service.submit(
        QuestionCreate(
            question="A synthetic guideline question",
            source_filters=SourceFilters(jurisdictions=["TEST"]),
        )
    )
    events = [event async for event in service.events(accepted.question_id)]
    result = service.result(accepted.question_id)

    assert events[-1].status is QuestionStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.reason_code == ServingAbstention.NO_ACTIVE_RELEASE.value
    assert result.claims == []
    await service.close()


async def test_retrieved_evidence_without_a_generation_lane_abstains() -> None:
    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(bundle, default_rankings())
    service = QuestionService(
        active_release_provider=lambda: _release(bundle, candidate),
        retrieval_engine=build_engine(reader, qdrant, candidate),
    )

    accepted = await service.submit(
        QuestionCreate(
            question="A synthetic guideline question",
            source_filters=SourceFilters(jurisdictions=["TEST"]),
        )
    )
    events = [event async for event in service.events(accepted.question_id)]
    result = service.result(accepted.question_id)

    assert events[-1].status is QuestionStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.reason_code == "GENERATION_UNAVAILABLE"
    assert result.abstention.closest_evidence_ids
    await service.close()


async def test_the_default_jurisdiction_filter_excludes_a_release_outside_it() -> None:
    """The request default is US/EU/UK, and a global corpus is in none of them.

    This is the shipped default of ``SourceFilters``, applied faithfully rather than
    quietly ignored, so a release whose jurisdiction sits outside that set retrieves
    nothing. Recorded as a test because the fix belongs in the filter the client sends,
    not in a serving path that decides to widen it.
    """

    bundle = fixture_bundle()
    candidate = fixture_candidate()
    reader = FakeReleaseReader(bundle, binding_for(bundle, candidate))
    qdrant = FakeServingQdrant(bundle, default_rankings())
    engine = build_engine(reader, qdrant, candidate)

    result = await engine.retrieve("synthetic guideline question", SourceFilters())

    assert SourceFilters().jurisdictions == ["US", "EU", "UK"]
    assert result.abstention is ServingAbstention.NO_EVIDENCE_RETRIEVED
    assert qdrant.requests, "the search still runs; the release simply matches nothing"


async def _release(bundle: CorpusReleaseBundle, candidate: RetrievalCandidateManifest):
    return binding_for(bundle, candidate).active_release()
