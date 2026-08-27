import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.corpus.releases import SQLCorpusReleaseRepository
from app.corpus_steward.candidate_schemas import (
    CandidateLane,
    CandidateLaneKind,
    CandidateReranker,
    RetrievalCandidateContent,
    RetrievalCandidateManifest,
)
from app.corpus_steward.cli import (
    index_attestation_schema_document,
    index_vector_schema_document,
)
from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.index_repository import (
    IndexBasisError,
    SQLIndexBasisRepository,
)
from app.corpus_steward.index_schemas import (
    DenseVectorDefinition,
    EmbeddingModelReference,
    EvidenceVectorRecord,
    IndexAttestationContent,
    IndexVectorBatch,
    IndexVectorBatchContent,
    SignedIndexAttestation,
    SparseVector,
    SparseVectorDefinition,
)
from app.corpus_steward.qdrant_index import (
    PAYLOAD_INDEXES,
    IndexValidationError,
    QdrantIndexService,
    _ExpectedPoint,
    candidate_qdrant_collection,
    candidate_vector_profile_sha256,
    stable_qdrant_point_id,
)
from app.corpus_steward.registry import SQLAttestationRepository
from app.corpus_steward.schemas import AttestationPurpose
from app.persistence.database import Database
from app.persistence.models import (
    CanonicalEvidenceRow,
    CorpusQARunRow,
    CorpusReleaseEvidenceRow,
    CorpusReleaseRow,
    EvidenceQADecisionRow,
    SourceRow,
)
from app.schemas.corpus import (
    CorpusReleaseBundle,
    canonical_json_bytes,
    canonical_sha256,
)
from app.schemas.domain import utc_now

FIXTURE_PATH = Path(__file__).parents[4] / "data" / "fixtures" / "corpus-release-v1.json"
VECTOR_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "qdrant-vector-batch-1.0.0.schema.json"
)
ATTESTATION_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "qdrant-index-attestation-1.0.0.schema.json"
)


def fixture_bundle() -> CorpusReleaseBundle:
    return CorpusReleaseBundle.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))


def vector_batch(bundle: CorpusReleaseBundle) -> IndexVectorBatch:
    records = []
    for position, evidence in enumerate(bundle.evidence, start=1):
        records.append(
            EvidenceVectorRecord(
                evidence_id=evidence.evidence_id,
                evidence_sha256=evidence.sha256,
                dense=(float(position), 0.25, 0.5),
                sparse=SparseVector(
                    indices=(position, position + 20),
                    values=(1.0, 0.5),
                ),
            )
        )
    model = EmbeddingModelReference(
        model_id="fixture/model",
        revision="fixture-revision-1",
        artifact_sha256="d" * 64,
    )
    return IndexVectorBatch.seal(
        IndexVectorBatchContent(
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            generated_at=utc_now(),
            dense=DenseVectorDefinition(dimension=3, model=model),
            sparse=SparseVectorDefinition(
                dimension=100,
                model=model.model_copy(update={"artifact_sha256": "e" * 64}),
            ),
            records=tuple(records),
        )
    )


def source_classes(bundle: CorpusReleaseBundle) -> dict[str, str]:
    return {item.source_id: "E1" for item in bundle.evidence}


def retrieval_candidate(vectors: IndexVectorBatch) -> RetrievalCandidateManifest:
    return RetrievalCandidateManifest.seal(
        RetrievalCandidateContent(
            candidate_id="fixture-qwen-candidate-v1",
            lanes=(
                CandidateLane(
                    lane_id="dense",
                    kind=CandidateLaneKind.DENSE,
                    vector_name=vectors.content.dense.name,
                    model=vectors.content.dense.model,
                    adapter_id="fixture/dense",
                    adapter_revision="1.0.0",
                ),
                CandidateLane(
                    lane_id="sparse",
                    kind=CandidateLaneKind.BM25,
                    vector_name=vectors.content.sparse.name,
                    model=vectors.content.sparse.model,
                    adapter_id="fixture/sparse",
                    adapter_revision="1.0.0",
                ),
            ),
        )
    )


def test_candidate_collection_identity_is_vector_profile_specific() -> None:
    bundle = fixture_bundle()
    vectors = vector_batch(bundle)
    first = retrieval_candidate(vectors)
    weighted = RetrievalCandidateManifest.seal(
        first.content.model_copy(
            update={
                "lanes": tuple(
                    lane.model_copy(update={"weight": 2.0})
                    if lane.kind is CandidateLaneKind.DENSE
                    else lane
                    for lane in first.content.lanes
                )
            }
        )
    )
    reranked = RetrievalCandidateManifest.seal(
        first.content.model_copy(
            update={
                "reranker": CandidateReranker(
                    model=EmbeddingModelReference(
                        model_id="Qwen/Qwen3-Reranker-0.6B",
                        revision="e" * 40,
                        artifact_sha256="f" * 64,
                    ),
                    adapter_id="med-rag/qwen3-reranker-transformers",
                    adapter_revision="1.0.0",
                    instruction_sha256="1" * 64,
                    candidate_pool=50,
                    output_depth=10,
                )
            }
        )
    )
    changed_vectors = vectors.model_copy(
        update={
            "content": vectors.content.model_copy(
                update={
                    "dense": vectors.content.dense.model_copy(
                        update={
                            "model": vectors.content.dense.model.model_copy(
                                update={"artifact_sha256": "a" * 64}
                            )
                        }
                    )
                }
            )
        }
    )
    changed = retrieval_candidate(changed_vectors)

    assert candidate_vector_profile_sha256(bundle, first) == (
        candidate_vector_profile_sha256(bundle, weighted)
    )
    assert candidate_qdrant_collection(bundle, first) == candidate_qdrant_collection(
        bundle, weighted
    )
    assert candidate_qdrant_collection(bundle, first) == candidate_qdrant_collection(
        bundle, reranked
    )
    assert candidate_qdrant_collection(bundle, first) != candidate_qdrant_collection(
        bundle, changed
    )
    assert len(candidate_qdrant_collection(bundle, first)) <= 255


class FakeQdrant:
    def __init__(self) -> None:
        self.collection: dict[str, Any] | None = None
        self.points: dict[str, dict[str, Any]] = {}
        self.upsert_calls = 0

    async def server_version(self) -> str:
        return "1.15.4"

    async def get_collection(self, collection: str) -> dict[str, Any] | None:
        return self.collection

    async def create_collection(
        self, collection: str, configuration: dict[str, Any]
    ) -> None:
        self.collection = {
            "status": "green",
            "optimizer_status": "ok",
            "points_count": 0,
            "config": {
                "params": {
                    "vectors": copy.deepcopy(configuration["vectors"]),
                    "sparse_vectors": copy.deepcopy(configuration["sparse_vectors"]),
                },
                "metadata": copy.deepcopy(configuration["metadata"]),
            },
            "payload_schema": {},
        }

    async def create_payload_index(
        self, collection: str, field_name: str, field_schema: str
    ) -> None:
        assert self.collection is not None
        self.collection["payload_schema"][field_name] = {"data_type": field_schema}

    async def upsert_points(
        self, collection: str, points: list[dict[str, Any]]
    ) -> None:
        assert self.collection is not None
        self.upsert_calls += 1
        dense_name = next(iter(self.collection["config"]["params"]["vectors"]))
        distance = self.collection["config"]["params"]["vectors"][dense_name]["distance"]
        for point in copy.deepcopy(points):
            if distance == "Cosine":
                dense = point["vector"][dense_name]
                norm = math.sqrt(sum(item * item for item in dense))
                point["vector"][dense_name] = [item / norm for item in dense]
            self.points[point["id"]] = point
        self.collection["points_count"] = len(self.points)

    async def scroll_points(
        self, collection: str, *, maximum: int
    ) -> list[dict[str, Any]]:
        return [copy.deepcopy(self.points[key]) for key in sorted(self.points)][:maximum]

    async def query_points(
        self, collection: str, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        using = request["using"]
        query = request["query"]

        def score(point: dict[str, Any]) -> float:
            vector = point["vector"][using]
            if isinstance(query, list):
                return sum(left * right for left, right in zip(query, vector, strict=True))
            query_values = dict(zip(query["indices"], query["values"], strict=True))
            return sum(
                query_values.get(index, 0.0) * value
                for index, value in zip(vector["indices"], vector["values"], strict=True)
            )

        ranked = sorted(self.points.values(), key=score, reverse=True)[: request["limit"]]
        # Real query_points always returns a score, and rank sealing depends on it.
        return [
            {
                "id": point["id"],
                "score": score(point),
                "payload": {
                    key: point["payload"][key]
                    for key in request["with_payload"]
                },
            }
            for point in ranked
        ]


async def test_build_is_complete_validated_and_idempotent() -> None:
    bundle = fixture_bundle()
    vectors = vector_batch(bundle)
    candidate = retrieval_candidate(vectors)
    qdrant = FakeQdrant()
    service = QdrantIndexService(qdrant, upsert_batch_size=2)

    first = await service.build(
        bundle, vectors, candidate, source_classes=source_classes(bundle)
    )
    second = await service.build(
        bundle, vectors, candidate, source_classes=source_classes(bundle)
    )

    assert first.content.outcome == "VALIDATED"
    assert first.content.qdrant_collection == candidate_qdrant_collection(
        bundle, candidate
    )
    assert first.content.qdrant_collection != bundle.manifest.content.qdrant_collection
    assert first.content.point_count == len(bundle.evidence)
    assert first.content.dense.dimension == 3
    assert first.content.sparse.dimension == 100
    assert len(first.content.smoke_results) == 3
    assert second.content.vector_set_sha256 == first.content.vector_set_sha256
    assert qdrant.upsert_calls == 2
    assert qdrant.collection is not None
    assert set(qdrant.collection["payload_schema"]) == set(PAYLOAD_INDEXES)
    assert {point["payload"]["evidence_id"] for point in qdrant.points.values()} == {
        item.evidence_id for item in bundle.evidence
    }
    assert all(
        point["payload"]["approval_status"] == "APPROVED"
        for point in qdrant.points.values()
    )


async def test_validation_rejects_payload_digest_drift_and_unexpected_points() -> None:
    bundle = fixture_bundle()
    vectors = vector_batch(bundle)
    candidate = retrieval_candidate(vectors)
    qdrant = FakeQdrant()
    service = QdrantIndexService(qdrant)
    await service.build(
        bundle, vectors, candidate, source_classes=source_classes(bundle)
    )
    first_id = sorted(qdrant.points)[0]
    qdrant.points[first_id]["payload"]["jurisdiction"] = "DRIFTED"
    unexpected_id = "00000000-0000-0000-0000-000000000000"
    qdrant.points[unexpected_id] = copy.deepcopy(qdrant.points[first_id])
    qdrant.points[unexpected_id]["id"] = unexpected_id
    assert qdrant.collection is not None
    qdrant.collection["points_count"] = len(bundle.evidence)

    with pytest.raises(IndexValidationError) as captured:
        await service.validate(
            bundle, vectors, candidate, source_classes=source_classes(bundle)
        )

    assert any(
        blocker.startswith("UNEXPECTED_POINT:") for blocker in captured.value.blockers
    )
    assert any(
        blocker.startswith("POINT_PAYLOAD_MISMATCH:")
        for blocker in captured.value.blockers
    )


async def test_build_refuses_existing_collection_config_drift() -> None:
    bundle = fixture_bundle()
    vectors = vector_batch(bundle)
    candidate = retrieval_candidate(vectors)
    qdrant = FakeQdrant()
    service = QdrantIndexService(qdrant)
    await service.build(
        bundle, vectors, candidate, source_classes=source_classes(bundle)
    )
    assert qdrant.collection is not None
    qdrant.collection["config"]["params"]["vectors"]["dense"]["size"] = 4

    with pytest.raises(IndexValidationError) as captured:
        await service.build(
            bundle, vectors, candidate, source_classes=source_classes(bundle)
        )

    assert captured.value.blockers == ("DENSE_VECTOR_CONFIG_MISMATCH",)


def test_vector_batch_rejects_evidence_digest_mismatch_before_writes() -> None:
    bundle = fixture_bundle()
    vectors = vector_batch(bundle)
    raw = json.loads(vectors.model_dump_json())
    raw["content"]["records"][0]["evidence_sha256"] = "0" * 64
    raw["batch_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="vector batch digest"):
        IndexVectorBatch.model_validate(raw)


def test_stable_qdrant_point_ids_are_uuid_and_release_independent() -> None:
    evidence_id = "EV_STABLE_001"

    assert stable_qdrant_point_id(evidence_id) == stable_qdrant_point_id(evidence_id)
    assert len(stable_qdrant_point_id(evidence_id)) == 36
    assert stable_qdrant_point_id(evidence_id) != stable_qdrant_point_id("EV_STABLE_002")


def test_checked_in_index_schemas_match_versioned_contracts() -> None:
    assert json.loads(VECTOR_SCHEMA_PATH.read_text(encoding="utf-8")) == (
        index_vector_schema_document()
    )
    assert json.loads(ATTESTATION_SCHEMA_PATH.read_text(encoding="utf-8")) == (
        index_attestation_schema_document()
    )


async def seed_registered_index_basis(
    database: Database, bundle: CorpusReleaseBundle
) -> None:
    now = utc_now()
    content = bundle.manifest.content
    bundle_sha256 = canonical_sha256(bundle)
    async with database.session() as session:
        for source_id in sorted({item.source_id for item in bundle.evidence}):
            session.add(
                SourceRow(
                    source_id=source_id,
                    publisher_id="PUB_FIXTURE",
                    title="Fixture source",
                    source_class="E1",
                    jurisdiction="TEST",
                    canonical_url="https://fixtures.invalid/source",
                    license_render_allowed=True,
                    created_at=now,
                )
            )
        session.add(
            CorpusReleaseRow(
                corpus_release_id=content.corpus_release_id,
                contract_version=content.schema_version,
                manifest_sha256=bundle.manifest.manifest_sha256,
                manifest=bundle.manifest.model_dump(mode="json"),
                state="VALIDATED",
                previous_release_id=None,
                qdrant_collection=content.qdrant_collection,
                cutoff_at=content.cutoff_at,
                index_status="NOT_BUILT",
                index_point_count=None,
                index_attestation_sha256=None,
                index_validated_at=None,
                validated_at=now,
                activated_at=None,
                activated_by=None,
                activation_decision_sha256=None,
                created_at=now,
            )
        )
        for evidence in bundle.evidence:
            session.add(
                CanonicalEvidenceRow(
                    evidence_id=evidence.evidence_id,
                    evidence_sha256=evidence.sha256,
                    source_id=evidence.source_id,
                    source_version_id=evidence.source_version_id,
                    approval_status="APPROVED",
                    payload=evidence.model_dump(mode="json"),
                    created_at=now,
                )
            )
            session.add(
                CorpusReleaseEvidenceRow(
                    corpus_release_id=content.corpus_release_id,
                    evidence_id=evidence.evidence_id,
                )
            )
        session.add(
            CorpusQARunRow(
                qa_run_id="QA_FIXTURE_INDEX",
                corpus_release_candidate_id="CRC_FIXTURE_INDEX",
                materialization_run_id="MAT_FIXTURE_INDEX",
                corpus_release_candidate_sha256="1" * 64,
                state="VALIDATED",
                evidence_manifest_sha256="2" * 64,
                evidence_manifest={},
                evidence_manifest_artifact_sha256="2" * 64,
                evidence_manifest_attestation_id="ATT_FIXTURE_MANIFEST",
                evidence_count=len(bundle.evidence) + 1,
                decision_batch_sha256="3" * 64,
                decision_batch={},
                decision_batch_artifact_sha256="3" * 64,
                decision_batch_attestation_id="ATT_FIXTURE_DECISIONS",
                approved_count=len(bundle.evidence),
                quarantined_count=1,
                corpus_release_id=content.corpus_release_id,
                bundle_sha256=bundle_sha256,
                bundle_artifact_sha256=bundle_sha256,
                started_at=now,
                completed_at=now,
            )
        )
        decisions = [(item.evidence_id, "APPROVE") for item in bundle.evidence]
        decisions.append(("EV_FIXTURE_QUARANTINED", "QUARANTINE"))
        for evidence_id, disposition in decisions:
            session.add(
                EvidenceQADecisionRow(
                    qa_run_id="QA_FIXTURE_INDEX",
                    evidence_id=evidence_id,
                    materialized_evidence_sha256="4" * 64,
                    decision_sha256=hashlib.sha256(evidence_id.encode()).hexdigest(),
                    disposition=disposition,
                    decision_authority="fixture-deterministic-qa",
                    batch_attestation_id="ATT_FIXTURE_DECISIONS",
                    payload={},
                    decided_at=now,
                )
            )


async def test_index_basis_reconciles_release_bundle_and_all_qa_decisions(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'index-basis.sqlite3'}")
    await database.create_schema_for_tests()
    bundle = fixture_bundle()
    await seed_registered_index_basis(database, bundle)
    repository = SQLIndexBasisRepository(database)

    basis = await repository.load(bundle.manifest.content.corpus_release_id)
    repository.require_bundle_match(basis, bundle)

    assert basis.bundle == bundle
    assert basis.materialized_count == 4
    assert basis.approved_count == 3
    assert basis.quarantined_count == 1
    assert basis.release.index_status == "NOT_BUILT"
    assert basis.source_classes == {"SRC_FIXTURE_001": "E1"}

    async with database.session() as session:
        qa_run = await session.get(CorpusQARunRow, "QA_FIXTURE_INDEX")
        assert qa_run is not None
        qa_run.approved_count = 2
    with pytest.raises(IndexBasisError, match="counts do not reconcile"):
        await repository.load(bundle.manifest.content.corpus_release_id)
    await database.close()


async def test_signed_attestation_registers_validated_index_without_activation(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'index-attest.sqlite3'}")
    await database.create_schema_for_tests()
    bundle = fixture_bundle()
    await seed_registered_index_basis(database, bundle)
    basis = await SQLIndexBasisRepository(database).load(
        bundle.manifest.content.corpus_release_id
    )
    vectors = vector_batch(bundle)
    candidate = retrieval_candidate(vectors)
    report = await QdrantIndexService(FakeQdrant()).build(
        bundle,
        vectors,
        candidate,
        source_classes=basis.source_classes,
    )
    signer = Ed25519Signer(
        key_id="fixture-index-stage-key",
        signer_identity="fixture-index-steward",
        private_key=Ed25519PrivateKey.generate(),
    )
    attestations = SQLAttestationRepository(database)
    await attestations.register_key(
        key_id=signer.key_id,
        signer_identity=signer.signer_identity,
        public_key_pem=signer.public_key_pem(),
        purposes=(AttestationPurpose.STAGE,),
    )
    content = IndexAttestationContent(
        corpus_release_id=basis.release.corpus_release_id,
        manifest_sha256=basis.release.manifest_sha256,
        qdrant_collection=report.content.qdrant_collection,
        validation_report_sha256=report.report_sha256,
        qa_run_id=basis.qa_run_id,
        materialized_count=basis.materialized_count,
        approved_count=basis.approved_count,
        quarantined_count=basis.quarantined_count,
        point_count=report.content.point_count,
        attested_at=report.content.validated_at,
    )
    reference = await attestations.record_and_verify(
        content,
        signer.sign(canonical_json_bytes(content)),
        purpose=AttestationPurpose.STAGE,
        predicate_type="https://med-rag.local/attestations/qdrant-index-validation",
    )
    signed = SignedIndexAttestation(
        content=content,
        statement_sha256=canonical_sha256(content),
        validation_report=report,
        attestation=reference,
    )

    release = await SQLCorpusReleaseRepository(database).mark_index_validated(
        basis.release.corpus_release_id,
        point_count=signed.content.point_count,
        index_attestation_sha256=signed.statement_sha256,
    )

    assert release.index_status == "VALIDATED"
    assert release.index_attestation_sha256 == signed.statement_sha256
    assert release.state.value == "VALIDATED"
    assert await SQLCorpusReleaseRepository(database).active_release() is None
    await database.close()


def _smoke_result(point_id: str, score: float, *, evidence_id: str = "EV_1") -> dict[str, Any]:
    return {
        "id": point_id,
        "score": score,
        "payload": {"evidence_id": evidence_id, "evidence_sha256": "a" * 64},
    }


def _smoke_expected(point_id: str, *, evidence_id: str = "EV_1") -> _ExpectedPoint:
    return _ExpectedPoint(
        evidence_id=evidence_id,
        point_id=point_id,
        payload={"evidence_sha256": "a" * 64},
        dense=(1.0,),
        sparse_indices=(0,),
        sparse_values=(1.0,),
        vector_sha256="b" * 64,
    )


def test_sealed_rank_ignores_the_order_the_server_returned_ties_in() -> None:
    """Tied results must not let server ordering move a sealed rank.

    Qdrant does not promise a stable order among exact ties. A controlled
    1.15.4-vs-1.19.0 comparison produced five different permutations across five
    rebuilds of identical data, and the rank is sealed into the validation report
    digest and from there into a signed attestation. The realistic trigger is duplicate
    evidence text embedding identically and both scoring 1.0 against a self-retrieval
    probe, so ties are broken here on point ID instead.
    """

    rank = QdrantIndexService._result_rank
    expected = _smoke_expected("p-bbb")
    tied = [
        _smoke_result("p-aaa", 1.0, evidence_id="EV_0"),
        _smoke_result("p-bbb", 1.0),
        _smoke_result("p-ccc", 1.0, evidence_id="EV_2"),
    ]

    # p-aaa sorts before p-bbb, p-ccc after, so the rank is 2 in every permutation.
    for permutation in itertools.permutations(tied):
        assert rank(list(permutation), expected) == 2

    # A strictly better score still outranks, and a worse one never does.
    outranked = [
        _smoke_result("p-zzz", 2.0, evidence_id="EV_9"),
        _smoke_result("p-bbb", 1.0),
    ]
    assert rank(outranked, expected) == 2
    assert rank(list(reversed(outranked)), expected) == 2


def test_sealed_rank_rejects_a_result_carrying_no_usable_score() -> None:
    """A missing or non-finite score cannot be ranked, so it fails closed."""

    rank = QdrantIndexService._result_rank
    expected = _smoke_expected("p-bbb")

    unscored = [{"id": "p-bbb", "payload": {"evidence_id": "EV_1", "evidence_sha256": "a" * 64}}]
    assert rank(unscored, expected) is None

    infinite = [
        _smoke_result("p-aaa", float("inf"), evidence_id="EV_0"),
        _smoke_result("p-bbb", 1.0),
    ]
    assert rank(infinite, expected) is None
