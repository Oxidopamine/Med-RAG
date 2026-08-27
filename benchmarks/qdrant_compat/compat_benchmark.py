"""Controlled Qdrant 1.15.4 versus 1.19.x compatibility and relevance benchmark.

Runs the production collection-creation, index-validation, and self-retrieval
smoke-test code paths (``QdrantIndexService`` over ``QdrantRESTClient``) against
two isolated servers and diffs the contract surfaces the roadmap names:
collection creation, payload-index parity, sparse-vector/BM25-IDF scoring, and
whether a signed index attestation sealed on the baseline still reconciles.

This script reports. It never changes the pin and never writes to the serving
instance on 6333.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from app.corpus_steward.crypto import Ed25519Signer  # noqa: E402
from app.corpus_steward.index_schemas import (  # noqa: E402
    IndexAttestationContent,
    IndexValidationReport,
    SignedIndexAttestation,
)
from app.corpus_steward.qdrant_index import (  # noqa: E402
    QDRANT_PINNED_VERSION,
    IndexValidationError,
    QdrantIndexError,
    QdrantIndexService,
    QdrantRESTClient,
    candidate_qdrant_collection,
)
from app.corpus_steward.schemas import (  # noqa: E402
    AttestationPurpose,
    VerifiedAttestationReference,
)
from app.schemas.corpus import canonical_json_bytes, canonical_sha256  # noqa: E402
from app.schemas.domain import utc_now  # noqa: E402

# Reuse the exact fixture builders the unit suite uses, so both targets are fed
# byte-identical bundles, vector batches, and candidate manifests.
_TEST_MODULE_PATH = API_ROOT / "tests" / "unit" / "test_qdrant_indexing.py"
_spec = importlib.util.spec_from_file_location("_qdrant_fixtures", _TEST_MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)

fixture_bundle = _fixtures.fixture_bundle
vector_batch = _fixtures.vector_batch
source_classes = _fixtures.source_classes
retrieval_candidate = _fixtures.retrieval_candidate

TRANSIENT_BLOCKERS = {"COLLECTION_NOT_GREEN", "COLLECTION_OPTIMIZER_NOT_OK"}


class Probe:
    """Accumulates per-target observations keyed by contract area."""

    def __init__(self, label: str, url: str) -> None:
        self.label = label
        self.url = url
        self.data: dict[str, Any] = {"label": label, "url": url}

    def record(self, key: str, value: Any) -> None:
        self.data[key] = value


async def raw_request(client: QdrantRESTClient, method: str, path: str) -> Any:
    try:
        return await client._request(method, path)
    except QdrantIndexError as error:
        return {"error": str(error)}


async def build_with_transient_retry(
    service: QdrantIndexService,
    bundle: Any,
    vectors: Any,
    candidate: Any,
    *,
    classes: dict[str, str],
    attempts: int = 12,
    delay: float = 1.0,
) -> tuple[Any, int]:
    """Call the real build path, tolerating only optimizer/status settling."""

    last: IndexValidationError | None = None
    for attempt in range(1, attempts + 1):
        try:
            report = await service.build(
                bundle, vectors, candidate, source_classes=classes
            )
            return report, attempt
        except IndexValidationError as error:
            if not set(error.blockers) <= TRANSIENT_BLOCKERS:
                raise
            last = error
            await asyncio.sleep(delay)
    assert last is not None
    raise last


async def reset_instance(url: str) -> list[str]:
    """Drop every collection on a benchmark instance so creation is exercised.

    Only ever pointed at the throwaway compat containers; the serving instance
    is not a valid target for this.
    """

    client = QdrantRESTClient(url)
    try:
        listing = await raw_request(client, "GET", "/collections")
        names = [
            item["name"]
            for item in ((listing or {}).get("result") or {}).get("collections", [])
        ]
        for name in names:
            await raw_request(client, "DELETE", f"/collections/{name}")
        return names
    finally:
        await client.close()


async def collect_contract_probe(probe: Probe, fixtures: dict[str, Any]) -> None:
    """Exercise creation, validation, payload indexes, points, and smoke tests."""

    # One fixture set is built once and shared by both targets. Rebuilding it
    # per target would stamp a fresh generated_at into the vector batch, which
    # changes batch_sha256 and makes every digest comparison meaningless.
    bundle = fixtures["bundle"]
    vectors = fixtures["vectors"]
    candidate = fixtures["candidate"]
    classes = fixtures["classes"]
    collection = candidate_qdrant_collection(bundle, candidate)
    probe.record("collection", collection)

    client = QdrantRESTClient(probe.url)
    try:
        server_version = await client.server_version()
        probe.record("server_version", server_version)

        # C0 -- the fail-closed version gate, exercised with the shipped pin.
        pinned = QdrantIndexService(client)
        gate: dict[str, Any] = {"expected": QDRANT_PINNED_VERSION}
        try:
            await pinned.build(bundle, vectors, candidate, source_classes=classes)
            gate["outcome"] = "ACCEPTED"
            gate["blockers"] = []
        except IndexValidationError as error:
            gate["outcome"] = "REFUSED"
            gate["blockers"] = list(error.blockers)
        gate["collections_after_gate"] = await raw_request(client, "GET", "/collections")
        probe.record("version_gate", gate)

        # Everything below overrides the pin so the remaining contracts can be
        # observed on the candidate at all. That override is a harness device,
        # not a proposed production change.
        service = QdrantIndexService(client, expected_qdrant_version=server_version)

        started = time.monotonic()
        try:
            report, attempts = await build_with_transient_retry(
                service, bundle, vectors, candidate, classes=classes
            )
            probe.record(
                "build",
                {
                    "outcome": "VALIDATED",
                    "attempts": attempts,
                    "seconds": round(time.monotonic() - started, 3),
                    "report_sha256": report.report_sha256,
                    "report_content": json.loads(report.content.model_dump_json()),
                },
            )
        except (IndexValidationError, QdrantIndexError) as error:
            probe.record(
                "build",
                {
                    "outcome": "FAILED",
                    "error_type": type(error).__name__,
                    "blockers": list(getattr(error, "blockers", ())) or [str(error)],
                },
            )
            probe.record(
                "raw_collection",
                await raw_request(client, "GET", f"/collections/{collection}"),
            )
            return

        # C1/C2 -- raw server echo of the created collection and its indexes.
        raw_collection = await raw_request(
            client, "GET", f"/collections/{collection}"
        )
        probe.record("raw_collection", raw_collection)
        result = (raw_collection or {}).get("result") or {}
        probe.record("raw_params", (result.get("config") or {}).get("params"))
        probe.record("raw_metadata", (result.get("config") or {}).get("metadata"))
        probe.record("payload_schema", result.get("payload_schema"))
        probe.record(
            "collection_counts",
            {
                key: result.get(key)
                for key in (
                    "status",
                    "optimizer_status",
                    "points_count",
                    "indexed_vectors_count",
                    "segments_count",
                )
            },
        )

        # C3 -- idempotent re-build and an independent re-validate.
        rebuild, _ = await build_with_transient_retry(
            service, bundle, vectors, candidate, classes=classes
        )
        revalidate = await service.validate(
            bundle, vectors, candidate, source_classes=classes
        )
        probe.record(
            "idempotence",
            {
                "rebuild_report_sha256": rebuild.report_sha256,
                "revalidate_report_sha256": revalidate.report_sha256,
                "vector_set_sha256": revalidate.content.vector_set_sha256,
                "payload_set_sha256": revalidate.content.payload_set_sha256,
                "collection_config_sha256": revalidate.content.collection_config_sha256,
                "point_id_set_sha256": revalidate.content.point_id_set_sha256,
                "evidence_set_sha256": revalidate.content.evidence_set_sha256,
                "smoke_results": [
                    json.loads(item.model_dump_json())
                    for item in revalidate.content.smoke_results
                ],
            },
        )

        # C3b -- raw scroll echo, to catch response-shape drift.
        scrolled = await client.scroll_points(collection, maximum=16)
        probe.record(
            "scroll_shape",
            {
                "count": len(scrolled),
                "point_keys": sorted({key for point in scrolled for key in point}),
                "vector_container_keys": sorted(
                    {
                        key
                        for point in scrolled
                        if isinstance(point.get("vector"), dict)
                        for key in point["vector"]
                    }
                ),
            },
        )
        probe.record(
            "points",
            {
                str(point["id"]): {
                    "payload": point.get("payload"),
                    "vector": point.get("vector"),
                }
                for point in scrolled
            },
        )

        # C4/C5 -- raw dense and sparse scores, which the sealed report discards.
        probe.record("scores", await score_probe(client, collection, vectors, scrolled))

        # C6 -- filtering parity across the indexed payload field shapes.
        probe.record("filters", await filter_probe(client, collection, bundle, vectors))
    finally:
        await client.close()

    # C4b -- purpose-built IDF probe on a designed term-frequency distribution.
    idf_client = QdrantRESTClient(probe.url)
    try:
        probe.record("idf_probe", await idf_probe(idf_client))
        probe.record("determinism", await determinism_probe(idf_client))
    finally:
        await idf_client.close()


async def score_probe(
    client: QdrantRESTClient,
    collection: str,
    vectors: Any,
    scrolled: list[dict[str, Any]],
) -> dict[str, Any]:
    """Capture raw dense and sparse scores for every stored point as a query."""

    dense_name = vectors.content.dense.name
    sparse_name = vectors.content.sparse.name
    out: dict[str, Any] = {"dense": {}, "sparse": {}}
    for point in sorted(scrolled, key=lambda item: str(item["id"])):
        vector = point.get("vector") or {}
        dense_query = vector.get(dense_name)
        sparse_query = vector.get(sparse_name)
        if isinstance(dense_query, list):
            results = await client.query_points(
                collection,
                {
                    "query": dense_query,
                    "using": dense_name,
                    "limit": 16,
                    "with_payload": ["evidence_id"],
                    "with_vector": False,
                },
            )
            out["dense"][str(point["id"])] = [
                {"id": str(item.get("id")), "score": repr(item.get("score"))}
                for item in results
            ]
        if isinstance(sparse_query, dict):
            results = await client.query_points(
                collection,
                {
                    "query": {
                        "indices": sparse_query.get("indices"),
                        "values": sparse_query.get("values"),
                    },
                    "using": sparse_name,
                    "limit": 16,
                    "with_payload": ["evidence_id"],
                    "with_vector": False,
                },
            )
            out["sparse"][str(point["id"])] = [
                {"id": str(item.get("id")), "score": repr(item.get("score"))}
                for item in results
            ]
    return out


async def filter_probe(
    client: QdrantRESTClient, collection: str, bundle: Any, vectors: Any
) -> dict[str, Any]:
    """Compare filtered retrieval across every indexed payload field shape."""

    release = vectors.content.corpus_release_id
    evidence = sorted(bundle.evidence, key=lambda item: item.evidence_id)
    sample = evidence[0]
    dense_name = vectors.content.dense.name
    query = [1.0] * vectors.content.dense.dimension

    cases: dict[str, Any] = {
        "production_smoke_filter": {
            "must": [
                {"key": "corpus_release_id", "match": {"value": release}},
                {"key": "approval_status", "match": {"value": "APPROVED"}},
            ]
        },
        "keyword_match": {
            "must": [{"key": "evidence_id", "match": {"value": sample.evidence_id}}]
        },
        "array_keyword_match": {
            "must": [
                {
                    "key": "evidence_roles",
                    "match": {"value": sample.evidence_roles[0].value},
                }
            ]
        },
        "match_any": {
            "must": [
                {
                    "key": "evidence_id",
                    "match": {"any": [item.evidence_id for item in evidence[:2]]},
                }
            ]
        },
        "must_not": {
            "must_not": [
                {"key": "evidence_id", "match": {"value": sample.evidence_id}}
            ]
        },
        "unindexed_bool": {
            "must": [{"key": "render_allowed", "match": {"value": True}}]
        },
        "unindexed_nested_is_empty": {
            "must": [{"is_empty": {"key": "applicability.population"}}]
        },
        "unindexed_nested_match_any": {
            "must": [
                {"key": "applicability.care_settings", "match": {"any": ["INPATIENT"]}}
            ]
        },
        "no_match_control": {
            "must": [
                {"key": "jurisdiction", "match": {"value": "NO_SUCH_JURISDICTION"}}
            ]
        },
    }

    out: dict[str, Any] = {}
    for name, condition in cases.items():
        try:
            results = await client.query_points(
                collection,
                {
                    "query": query,
                    "using": dense_name,
                    "filter": condition,
                    "limit": 16,
                    "with_payload": ["evidence_id"],
                    "with_vector": False,
                },
            )
            out[name] = {
                "ids": sorted(str(item.get("id")) for item in results),
                "count": len(results),
            }
        except QdrantIndexError as error:
            out[name] = {"error": str(error)}
    return out


def idf_corpus() -> list[dict[str, Any]]:
    """Twelve documents with a deliberately skewed document-frequency profile.

    Term 1 is in every document, terms 2 and 5 in half, term 3 in a quarter, and
    terms 4, 6, 8 are hapax. IDF weighting must therefore separate them, so a
    change to the IDF formula shows up as a score change even when ranks hold.
    """

    docs: list[dict[str, Any]] = []
    for index in range(12):
        indices = [1]
        values = [1.0 + index * 0.01]
        if index < 6:
            indices.append(2)
            values.append(0.75)
        else:
            indices.append(5)
            values.append(0.75)
        if index < 3:
            indices.append(3)
            values.append(0.5)
        if index == 0:
            indices.append(4)
            values.append(2.0)
        if index == 11:
            indices.append(6)
            values.append(2.0)
        if index in (3, 7):
            indices.append(7)
            values.append(1.25)
        if index == 5:
            indices.append(8)
            values.append(3.0)
        order = sorted(range(len(indices)), key=lambda position: indices[position])
        docs.append(
            {
                "id": index + 1,
                "vector": {
                    "text": {
                        "indices": [indices[position] for position in order],
                        "values": [values[position] for position in order],
                    }
                },
                "payload": {"doc": index},
            }
        )
    return docs


async def idf_probe(client: QdrantRESTClient) -> dict[str, Any]:
    """Score the same sparse queries under modifier=idf and modifier=none.

    The ``none`` arm is the control: it isolates a change in the IDF weighting
    from a change in plain sparse dot-product evaluation.
    """

    docs = idf_corpus()
    queries = {
        "common_and_rare": {"indices": [1, 4], "values": [1.0, 1.0]},
        "mid_frequency": {"indices": [2, 5], "values": [1.0, 1.0]},
        "broad": {"indices": [1, 2, 3, 4, 7], "values": [1.0, 1.0, 1.0, 1.0, 1.0]},
    }
    out: dict[str, Any] = {"corpus_sha256": canonical_sha256(docs)}
    for modifier in ("idf", "none"):
        collection = f"compat_idf_probe_{modifier}"
        await raw_request(client, "DELETE", f"/collections/{collection}")
        await client.create_collection(
            collection,
            {"vectors": {}, "sparse_vectors": {"text": {"modifier": modifier}}},
        )
        await client.upsert_points(collection, docs)
        scores: dict[str, Any] = {}
        for name, query in queries.items():
            results = await client.query_points(
                collection,
                {
                    "query": query,
                    "using": "text",
                    "limit": 12,
                    "with_payload": ["doc"],
                    "with_vector": False,
                },
            )
            scores[name] = [
                {"id": item.get("id"), "score": repr(item.get("score"))}
                for item in results
            ]
        out[modifier] = scores
    return out


async def determinism_probe(client: QdrantRESTClient, trials: int = 5) -> dict[str, Any]:
    """Rebuild the same collection repeatedly and check result reproducibility.

    Separates two things the sealed report conflates. Are the *scores* stable
    and identical, and is the *order* stable? ``RetrievalSmokeResult`` seals a
    rank, so an unstable order makes a report digest non-reproducible even when
    every score is unchanged.

    The dense arm deliberately includes duplicate vectors, because duplicated
    evidence text is the realistic way a release collection produces a tie at
    the top of a self-retrieval smoke test.
    """

    sparse_docs = idf_corpus()
    sparse_queries = {
        "sparse_distinct_scores": {"indices": [1, 4], "values": [1.0, 1.0]},
        "sparse_all_tied": {"indices": [2, 5], "values": [1.0, 1.0]},
    }
    # Four documents share one vector; the fifth is distinct.
    dense_docs = [
        {"id": index + 1, "vector": {"dense": vector}, "payload": {"doc": index}}
        for index, vector in enumerate(
            [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        )
    ]
    dense_queries = {"dense_duplicate_vectors": [1.0, 0.0, 0.0]}

    orders: dict[str, set[tuple[Any, ...]]] = {
        name: set() for name in list(sparse_queries) + list(dense_queries)
    }
    score_maps: dict[str, set[tuple[Any, ...]]] = {
        name: set() for name in list(sparse_queries) + list(dense_queries)
    }

    for _ in range(trials):
        await raw_request(client, "DELETE", "/collections/compat_determinism_sparse")
        await client.create_collection(
            "compat_determinism_sparse",
            {"vectors": {}, "sparse_vectors": {"text": {"modifier": "idf"}}},
        )
        await client.upsert_points(
            "compat_determinism_sparse",
            [
                {**doc, "vector": {"text": doc["vector"]["text"]}}
                for doc in sparse_docs
            ],
        )
        for name, query in sparse_queries.items():
            results = await client.query_points(
                "compat_determinism_sparse",
                {
                    "query": query,
                    "using": "text",
                    "limit": 12,
                    "with_payload": False,
                    "with_vector": False,
                },
            )
            orders[name].add(tuple(item.get("id") for item in results))
            score_maps[name].add(
                tuple(sorted((item.get("id"), repr(item.get("score"))) for item in results))
            )

        await raw_request(client, "DELETE", "/collections/compat_determinism_dense")
        await client.create_collection(
            "compat_determinism_dense",
            {"vectors": {"dense": {"size": 3, "distance": "Cosine"}}},
        )
        await client.upsert_points("compat_determinism_dense", dense_docs)
        for name, query in dense_queries.items():
            results = await client.query_points(
                "compat_determinism_dense",
                {
                    "query": query,
                    "using": "dense",
                    "limit": 5,
                    "with_payload": False,
                    "with_vector": False,
                },
            )
            orders[name].add(tuple(item.get("id") for item in results))
            score_maps[name].add(
                tuple(sorted((item.get("id"), repr(item.get("score"))) for item in results))
            )

    await raw_request(client, "DELETE", "/collections/compat_determinism_sparse")
    await raw_request(client, "DELETE", "/collections/compat_determinism_dense")

    return {
        "trials": trials,
        "cases": {
            name: {
                "distinct_orders": len(orders[name]),
                "distinct_score_maps": len(score_maps[name]),
                "orders": [list(item) for item in sorted(orders[name])],
                "score_map": sorted(next(iter(score_maps[name])))
                if len(score_maps[name]) == 1
                else None,
            }
            for name in orders
        },
    }


def attestation_probe(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Seal and sign an attestation on the baseline, then test it against 1.19.

    Two distinct questions are separated here. Does the already-signed statement
    still verify as an object (it is self-contained, so it should)? And does
    re-validating the live collection on the candidate still reconcile with the
    digest that statement covers (that is the contract that actually gates a
    re-index or a re-activation)?
    """

    if baseline.get("build", {}).get("outcome") != "VALIDATED":
        return {"status": "SKIPPED", "reason": "baseline build did not validate"}
    if candidate.get("build", {}).get("outcome") != "VALIDATED":
        return {"status": "SKIPPED", "reason": "candidate build did not validate"}

    baseline_report = IndexValidationReport.model_validate(
        {
            "content": baseline["build"]["report_content"],
            "report_sha256": baseline["build"]["report_sha256"],
        }
    )
    signer = Ed25519Signer(
        key_id="compat-benchmark-key",
        signer_identity="qdrant-compat-benchmark",
        private_key=Ed25519PrivateKey.generate(),
    )
    content = IndexAttestationContent(
        corpus_release_id=baseline_report.content.corpus_release_id,
        manifest_sha256=baseline_report.content.manifest_sha256,
        qdrant_collection=baseline_report.content.qdrant_collection,
        validation_report_sha256=baseline_report.report_sha256,
        qa_run_id="compat-benchmark-run",
        materialized_count=baseline_report.content.point_count,
        approved_count=baseline_report.content.point_count,
        quarantined_count=0,
        point_count=baseline_report.content.point_count,
        attested_at=utc_now(),
    )
    statement_sha256 = canonical_sha256(content)
    envelope = signer.sign(canonical_json_bytes(content))
    signed = SignedIndexAttestation(
        content=content,
        statement_sha256=statement_sha256,
        validation_report=baseline_report,
        attestation=VerifiedAttestationReference(
            attestation_id="compat-benchmark-attestation",
            purpose=AttestationPurpose.STAGE,
            predicate_type="https://med-rag.local/attestations/qdrant-index-validation",
            statement_sha256=statement_sha256,
            signature_sha256=envelope.signature_sha256,
            signer_identity=signer.signer_identity,
            signing_key_id=signer.key_id,
            verified_at=utc_now(),
        ),
    )

    candidate_content = candidate["build"]["report_content"]
    candidate_sha = candidate["build"]["report_sha256"]

    # Re-verify the sealed statement as a stored object.
    try:
        SignedIndexAttestation.model_validate(json.loads(signed.model_dump_json()))
        object_verifies = True
        object_error = None
    except ValueError as error:
        object_verifies = False
        object_error = str(error)

    # Now the operative question: does the candidate's fresh report reconcile
    # with the digest the signature covers?
    reconciles = candidate_sha == signed.content.validation_report_sha256

    # And isolate whether the version string is the ONLY reason it does not.
    baseline_content = dict(baseline["build"]["report_content"])
    neutralised_candidate = dict(candidate_content)
    for field in ("qdrant_version", "validated_at"):
        baseline_content.pop(field, None)
        neutralised_candidate.pop(field, None)
    version_only = baseline_content == neutralised_candidate

    differing = sorted(
        key
        for key in set(baseline["build"]["report_content"])
        | set(candidate_content)
        if baseline["build"]["report_content"].get(key) != candidate_content.get(key)
    )

    return {
        "status": "RUN",
        "signed_statement_sha256": statement_sha256,
        "baseline_report_sha256": signed.content.validation_report_sha256,
        "candidate_report_sha256": candidate_sha,
        "sealed_object_still_verifies": object_verifies,
        "sealed_object_error": object_error,
        "candidate_report_reconciles_with_signature": reconciles,
        "differs_only_by_version_and_timestamp": version_only,
        "differing_report_fields": differing,
    }


def compare(area: str, left: Any, right: Any, detail_pass: str, detail_fail: str) -> dict[str, Any]:
    equal = left == right
    return {
        "area": area,
        "verdict": "PASS" if equal else "FAIL",
        "detail": detail_pass if equal else detail_fail,
        "baseline": left,
        "candidate": right,
    }


def score_delta(left: Any, right: Any) -> dict[str, Any]:
    """Compare two score maps exactly, separating value drift from order drift.

    A changed score is a scoring-contract break: it would invalidate sealed
    vector batches. A changed order at *identical* scores is a weaker but
    distinct problem, because a sealed smoke-test rank is order-derived.
    """

    worst = 0.0
    mismatched: list[str] = []
    order_changes: list[str] = []
    tie_only_order_changes: list[str] = []
    for key in sorted(set(left) | set(right)):
        lefts = left.get(key) or []
        rights = right.get(key) or []
        if [item["id"] for item in lefts] != [item["id"] for item in rights]:
            order_changes.append(key)
            if len({item["score"] for item in lefts} | {item["score"] for item in rights}) == 1:
                tie_only_order_changes.append(key)
        by_id_left = {item["id"]: item["score"] for item in lefts}
        by_id_right = {item["id"]: item["score"] for item in rights}
        for point in sorted(set(by_id_left) | set(by_id_right)):
            raw_left = by_id_left.get(point)
            raw_right = by_id_right.get(point)
            if raw_left == raw_right:
                continue
            mismatched.append(f"{key}/{point}: {raw_left} -> {raw_right}")
            try:
                worst = max(worst, abs(float(raw_left) - float(raw_right)))
            except (TypeError, ValueError):
                worst = float("inf")
    return {
        "scores_identical": not mismatched,
        "order_identical": not order_changes,
        "identical": not mismatched and not order_changes,
        "max_abs_delta": worst,
        "order_changes": order_changes,
        "order_changes_explained_by_ties": tie_only_order_changes,
        "score_mismatches": mismatched[:40],
        "score_mismatch_count": len(mismatched),
    }


def scoring_finding(area: str, delta: dict[str, Any], subject: str) -> dict[str, Any]:
    """Grade a scoring comparison on score values, not on tie ordering."""

    if delta["scores_identical"] and delta["order_identical"]:
        return {
            "area": area,
            "verdict": "PASS",
            "detail": f"{subject}: scores and order bit-identical",
            "delta": delta,
        }
    if delta["scores_identical"] and delta["order_changes"] == delta[
        "order_changes_explained_by_ties"
    ]:
        return {
            "area": area,
            "verdict": "PASS",
            "detail": (
                f"{subject}: every score bit-identical; order differs only among "
                f"exactly-tied results ({delta['order_changes']})"
            ),
            "delta": delta,
        }
    if delta["scores_identical"]:
        return {
            "area": area,
            "verdict": "FAIL",
            "detail": (
                f"{subject}: scores identical but untied results reordered "
                f"({delta['order_changes']})"
            ),
            "delta": delta,
        }
    return {
        "area": area,
        "verdict": "FAIL",
        "detail": (
            f"{subject}: SCORE DRIFT, max_abs_delta={delta['max_abs_delta']}, "
            f"{delta['score_mismatch_count']} differing scores"
        ),
        "delta": delta,
    }


def determinism_finding(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    """Grade per-version reproducibility of repeated rebuilds."""

    findings: list[dict[str, Any]] = []
    base_cases = (baseline.get("determinism") or {}).get("cases") or {}
    cand_cases = (candidate.get("determinism") or {}).get("cases") or {}
    for name in sorted(set(base_cases) | set(cand_cases)):
        base = base_cases.get(name) or {}
        cand = cand_cases.get(name) or {}
        base_orders = base.get("distinct_orders")
        cand_orders = cand.get("distinct_orders")
        scores_stable = (
            base.get("distinct_score_maps") == 1 and cand.get("distinct_score_maps") == 1
        )
        same_scores = base.get("score_map") == cand.get("score_map")
        reproducible = cand_orders == 1
        findings.append(
            {
                "area": f"rebuild_determinism::{name}",
                "verdict": "PASS" if reproducible and scores_stable and same_scores else "FAIL",
                "detail": (
                    f"distinct orders over {(candidate.get('determinism') or {}).get('trials')}"
                    f" rebuilds: baseline={base_orders} candidate={cand_orders}; "
                    f"scores stable={scores_stable}, identical across versions={same_scores}"
                ),
                "baseline": base,
                "candidate": cand,
            }
        )
    return findings


def analyse(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    findings.append(
        {
            "area": "version_gate_fail_closed",
            "verdict": "PASS"
            if candidate.get("version_gate", {}).get("outcome") == "REFUSED"
            else "FAIL",
            "detail": (
                "shipped pin refuses the candidate before any write: "
                f"{candidate.get('version_gate', {}).get('blockers')}"
            ),
            "baseline": baseline.get("version_gate"),
            "candidate": candidate.get("version_gate"),
        }
    )

    base_build = baseline.get("build", {})
    cand_build = candidate.get("build", {})
    findings.append(
        {
            "area": "collection_creation",
            "verdict": "PASS"
            if base_build.get("outcome") == cand_build.get("outcome") == "VALIDATED"
            else "FAIL",
            "detail": (
                f"baseline={base_build.get('outcome')} "
                f"candidate={cand_build.get('outcome')} "
                f"blockers={cand_build.get('blockers')}"
            ),
            "baseline": base_build.get("outcome"),
            "candidate": cand_build.get("outcome"),
        }
    )

    findings.append(
        compare(
            "collection_config_echo",
            baseline.get("raw_params"),
            candidate.get("raw_params"),
            "config.params echoes identically, strict equality check holds",
            "config.params differs; QdrantIndexService compares this with != ",
        )
    )
    findings.append(
        compare(
            "collection_metadata_echo",
            baseline.get("raw_metadata"),
            candidate.get("raw_metadata"),
            "collection metadata round-trips identically",
            "collection metadata differs, COLLECTION_METADATA_MISMATCH risk",
        )
    )
    findings.append(
        compare(
            "payload_index_parity",
            baseline.get("payload_schema"),
            candidate.get("payload_schema"),
            "all payload indexes present with identical data_type",
            "payload_schema differs between versions",
        )
    )
    findings.append(
        compare(
            "point_and_vector_roundtrip",
            baseline.get("points"),
            candidate.get("points"),
            "payloads and stored vectors round-trip byte-identically",
            "stored payload or vector representation differs",
        )
    )
    findings.append(
        compare(
            "scroll_response_shape",
            baseline.get("scroll_shape"),
            candidate.get("scroll_shape"),
            "scroll response shape unchanged",
            "scroll response shape changed",
        )
    )
    findings.append(
        compare(
            "filtering",
            baseline.get("filters"),
            candidate.get("filters"),
            "every filter form returns identical id sets",
            "filter results differ",
        )
    )

    base_scores = baseline.get("scores") or {}
    cand_scores = candidate.get("scores") or {}
    findings.append(
        scoring_finding(
            "dense_scoring",
            score_delta(base_scores.get("dense") or {}, cand_scores.get("dense") or {}),
            "dense cosine on the release collection",
        )
    )
    findings.append(
        scoring_finding(
            "sparse_idf_scoring_release_collection",
            score_delta(
                base_scores.get("sparse") or {}, cand_scores.get("sparse") or {}
            ),
            "sparse IDF on the release collection",
        )
    )

    base_idf = baseline.get("idf_probe") or {}
    cand_idf = candidate.get("idf_probe") or {}
    findings.append(
        scoring_finding(
            "sparse_dot_product_control",
            score_delta(base_idf.get("none") or {}, cand_idf.get("none") or {}),
            "modifier=none control on the skewed-df corpus",
        )
    )
    findings.append(
        scoring_finding(
            "sparse_idf_scoring_designed_corpus",
            score_delta(base_idf.get("idf") or {}, cand_idf.get("idf") or {}),
            "modifier=idf on the skewed-df corpus",
        )
    )
    findings.extend(determinism_finding(baseline, candidate))

    attestation = attestation_probe(baseline, candidate)
    if attestation.get("status") != "RUN":
        verdict = "SKIP"
        detail = attestation.get("reason", "")
    elif attestation["candidate_report_reconciles_with_signature"]:
        verdict = "PASS"
        detail = "a report re-validated on the candidate matches the signed digest"
    else:
        verdict = "FAIL"
        detail = (
            "re-validation on the candidate does not reproduce the signed report "
            f"digest; differing fields={attestation['differing_report_fields']}"
        )
    findings.append(
        {
            "area": "signed_attestation_reconciliation",
            "verdict": verdict,
            "detail": detail,
            "probe": attestation,
        }
    )
    return findings


async def main() -> int:
    parser = argparse.ArgumentParser(description="Qdrant compatibility benchmark")
    parser.add_argument("--baseline-url", default="http://localhost:6343")
    parser.add_argument("--candidate-url", default="http://localhost:6344")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent
        / "qdrant-1.15.4-vs-1.19.0-report.json",
    )
    arguments = parser.parse_args()

    for url in (arguments.baseline_url, arguments.candidate_url):
        if "6333" in url:
            raise SystemExit("refusing to run against the serving instance on 6333")

    bundle = fixture_bundle()
    vectors = vector_batch(bundle)
    fixtures = {
        "bundle": bundle,
        "vectors": vectors,
        "candidate": retrieval_candidate(vectors),
        "classes": source_classes(bundle),
    }

    baseline = Probe("baseline-1.15.4", arguments.baseline_url)
    candidate = Probe("candidate-1.19.x", arguments.candidate_url)
    for probe in (baseline, candidate):
        dropped = await reset_instance(probe.url)
        probe.record("reset_dropped_collections", dropped)
        await collect_contract_probe(probe, fixtures)

    findings = analyse(baseline.data, candidate.data)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pinned_version": QDRANT_PINNED_VERSION,
        "baseline_version": baseline.data.get("server_version"),
        "candidate_version": candidate.data.get("server_version"),
        "baseline": baseline.data,
        "candidate": candidate.data,
        "findings": findings,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )

    print()
    print(
        f"baseline={baseline.data.get('server_version')} "
        f"candidate={candidate.data.get('server_version')}"
    )
    print(f"{'AREA':<42}{'VERDICT':<9}DETAIL")
    print("-" * 110)
    for finding in findings:
        print(f"{finding['area']:<42}{finding['verdict']:<9}{finding['detail'][:110]}")
    print(f"\nwrote {arguments.output}")
    return 0 if all(item["verdict"] in ("PASS", "SKIP") for item in findings) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
