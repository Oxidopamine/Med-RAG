"""Fail-closed Qdrant collection construction and exhaustive validation."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.corpus_steward.index_schemas import (
    INDEX_CONTRACT_VERSION,
    EvidenceVectorRecord,
    IndexValidationReport,
    IndexValidationReportContent,
    IndexVectorBatch,
    RetrievalSmokeResult,
)
from app.schemas.corpus import (
    CorpusEvidenceRecord,
    CorpusReleaseBundle,
    EvidenceApprovalStatus,
    canonical_sha256,
)
from app.schemas.domain import utc_now

QDRANT_PINNED_VERSION = "1.15.4"
POINT_ID_SCHEME = "uuid5-url:evidence-id:v1"
POINT_NAMESPACE = uuid5(NAMESPACE_URL, "https://med-rag.local/qdrant/evidence-id/v1")
PAYLOAD_INDEXES: dict[str, str] = {
    "approval_status": "keyword",
    "content_search_sha256": "keyword",
    "corpus_release_id": "keyword",
    "evidence_id": "keyword",
    "evidence_roles": "keyword",
    "evidence_sha256": "keyword",
    "jurisdiction": "keyword",
    "language": "keyword",
    "lifecycle_status": "keyword",
    "publisher_id": "keyword",
    "source_id": "keyword",
    "source_class": "keyword",
    "source_version_id": "keyword",
}


class QdrantIndexError(RuntimeError):
    pass


class IndexValidationError(QdrantIndexError):
    def __init__(self, blockers: list[str] | tuple[str, ...]) -> None:
        self.blockers = tuple(dict.fromkeys(blockers))
        super().__init__(", ".join(self.blockers))


class QdrantGateway(Protocol):
    async def server_version(self) -> str: ...

    async def get_collection(self, collection: str) -> dict[str, Any] | None: ...

    async def create_collection(
        self, collection: str, configuration: dict[str, Any]
    ) -> None: ...

    async def create_payload_index(
        self, collection: str, field_name: str, field_schema: str
    ) -> None: ...

    async def upsert_points(
        self, collection: str, points: list[dict[str, Any]]
    ) -> None: ...

    async def scroll_points(
        self, collection: str, *, maximum: int
    ) -> list[dict[str, Any]]: ...

    async def query_points(
        self, collection: str, request: dict[str, Any]
    ) -> list[dict[str, Any]]: ...


class QdrantRESTClient:
    """Small async client for the Qdrant operations used by the release builder."""

    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {"api-key": api_key} if api_key else None
        self._client = client or httpx.AsyncClient(
            base_url=url.rstrip("/"), headers=headers, timeout=timeout_seconds
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def server_version(self) -> str:
        payload = await self._request("GET", "/")
        version = payload.get("version")
        if not isinstance(version, str) or not version:
            raise QdrantIndexError("Qdrant root response does not contain a version")
        return version

    async def get_collection(self, collection: str) -> dict[str, Any] | None:
        try:
            response = await self._client.get(
                f"/collections/{quote(collection, safe='')}"
            )
        except httpx.RequestError as error:
            raise QdrantIndexError(
                f"Qdrant request failed: {error.__class__.__name__}"
            ) from error
        if response.status_code == 404:
            return None
        payload = self._decode_response(response)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise QdrantIndexError("Qdrant collection response is malformed")
        return result

    async def create_collection(
        self, collection: str, configuration: dict[str, Any]
    ) -> None:
        payload = await self._request(
            "PUT", f"/collections/{quote(collection, safe='')}", json=configuration
        )
        if payload.get("result") is not True:
            raise QdrantIndexError("Qdrant did not confirm collection creation")

    async def create_payload_index(
        self, collection: str, field_name: str, field_schema: str
    ) -> None:
        await self._request(
            "PUT",
            f"/collections/{quote(collection, safe='')}/index",
            params={"wait": "true"},
            json={"field_name": field_name, "field_schema": field_schema},
        )

    async def upsert_points(
        self, collection: str, points: list[dict[str, Any]]
    ) -> None:
        payload = await self._request(
            "PUT",
            f"/collections/{quote(collection, safe='')}/points",
            params={"wait": "true", "ordering": "strong"},
            json={"points": points},
        )
        result = payload.get("result")
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise QdrantIndexError("Qdrant point upsert did not complete")

    async def scroll_points(
        self, collection: str, *, maximum: int
    ) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        offset: int | str | None = None
        seen_offsets: set[int | str] = set()
        while True:
            body: dict[str, Any] = {
                "limit": min(256, maximum + 1),
                "with_payload": True,
                "with_vector": True,
            }
            if offset is not None:
                body["offset"] = offset
            payload = await self._request(
                "POST",
                f"/collections/{quote(collection, safe='')}/points/scroll",
                json=body,
            )
            result = payload.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("points"), list):
                raise QdrantIndexError("Qdrant scroll response is malformed")
            points.extend(result["points"])
            if len(points) > maximum:
                return points
            next_offset = result.get("next_page_offset")
            if next_offset is None:
                return points
            if not isinstance(next_offset, (int, str)) or next_offset in seen_offsets:
                raise QdrantIndexError("Qdrant scroll pagination is invalid")
            seen_offsets.add(next_offset)
            offset = next_offset

    async def query_points(
        self, collection: str, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        payload = await self._request(
            "POST",
            f"/collections/{quote(collection, safe='')}/points/query",
            json=request,
        )
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("points"), list):
            raise QdrantIndexError("Qdrant query response is malformed")
        return result["points"]

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as error:
            raise QdrantIndexError(
                f"Qdrant request failed: {error.__class__.__name__}"
            ) from error
        return self._decode_response(response)

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = response.text[:1000]
            raise QdrantIndexError(
                f"Qdrant returned HTTP {response.status_code}: {detail}"
            ) from error
        try:
            payload = response.json()
        except ValueError as error:
            raise QdrantIndexError("Qdrant returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise QdrantIndexError("Qdrant returned a non-object response")
        status = payload.get("status")
        if status not in (None, "ok"):
            raise QdrantIndexError(f"Qdrant operation failed: {status}")
        return payload


@dataclass(frozen=True)
class _ExpectedPoint:
    evidence_id: str
    point_id: str
    payload: dict[str, Any]
    dense: tuple[float, ...]
    sparse_indices: tuple[int, ...]
    sparse_values: tuple[float, ...]
    vector_sha256: str

    def qdrant_payload(self, *, dense_name: str, sparse_name: str) -> dict[str, Any]:
        return {
            "id": self.point_id,
            "payload": self.payload,
            "vector": {
                dense_name: list(self.dense),
                sparse_name: {
                    "indices": list(self.sparse_indices),
                    "values": list(self.sparse_values),
                },
            },
        }


def stable_qdrant_point_id(evidence_id: str) -> str:
    return str(uuid5(POINT_NAMESPACE, evidence_id))


def _float32(value: float) -> float:
    try:
        converted = struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError("vector value cannot be represented as float32") from error
    if not math.isfinite(converted):
        raise ValueError("vector value cannot be represented as a finite float32")
    return converted


def _vector_sha256(record: EvidenceVectorRecord) -> str:
    digest = hashlib.sha256()
    digest.update(struct.pack("<I", len(record.dense)))
    for value in record.dense:
        digest.update(struct.pack("<f", _float32(value)))
    digest.update(struct.pack("<I", len(record.sparse.indices)))
    for index, value in zip(record.sparse.indices, record.sparse.values, strict=True):
        digest.update(struct.pack("<I", index))
        digest.update(struct.pack("<f", _float32(value)))
    return digest.hexdigest()


class QdrantIndexService:
    def __init__(
        self,
        qdrant: QdrantGateway,
        *,
        expected_qdrant_version: str = QDRANT_PINNED_VERSION,
        upsert_batch_size: int = 64,
    ) -> None:
        if upsert_batch_size <= 0:
            raise ValueError("upsert_batch_size must be positive")
        self._qdrant = qdrant
        self._expected_qdrant_version = expected_qdrant_version
        self._upsert_batch_size = upsert_batch_size

    async def build(
        self,
        bundle: CorpusReleaseBundle,
        vectors: IndexVectorBatch,
        *,
        source_classes: Mapping[str, str],
        smoke_samples: int = 3,
        smoke_limit: int = 10,
    ) -> IndexValidationReport:
        expected = self._expected_points(bundle, vectors, source_classes=source_classes)
        collection = bundle.manifest.content.qdrant_collection
        version = await self._require_server_version()
        configuration = self._collection_configuration(bundle, vectors)
        collection_info = await self._qdrant.get_collection(collection)
        if collection_info is None:
            await self._qdrant.create_collection(collection, configuration)
            collection_info = await self._qdrant.get_collection(collection)
            if collection_info is None:
                raise QdrantIndexError("created Qdrant collection cannot be read back")

        self._validate_collection_configuration(
            collection_info, configuration, require_payload_indexes=False
        )
        existing = await self._qdrant.scroll_points(
            collection, maximum=len(expected) + 1
        )
        existing_ids = self._validate_points(existing, expected, vectors, require_complete=False)
        missing = [point for point_id, point in expected.items() if point_id not in existing_ids]
        for start in range(0, len(missing), self._upsert_batch_size):
            batch = missing[start : start + self._upsert_batch_size]
            await self._qdrant.upsert_points(
                collection,
                [
                    point.qdrant_payload(
                        dense_name=vectors.content.dense.name,
                        sparse_name=vectors.content.sparse.name,
                    )
                    for point in batch
                ],
            )

        payload_schema = collection_info.get("payload_schema")
        if not isinstance(payload_schema, dict):
            raise IndexValidationError(["COLLECTION_PAYLOAD_SCHEMA_MALFORMED"])
        unexpected_indexes = set(payload_schema) - set(PAYLOAD_INDEXES)
        if unexpected_indexes:
            raise IndexValidationError(
                [f"UNEXPECTED_PAYLOAD_INDEX:{item}" for item in sorted(unexpected_indexes)]
            )
        for field_name, field_schema in PAYLOAD_INDEXES.items():
            if field_name not in payload_schema:
                await self._qdrant.create_payload_index(
                    collection, field_name, field_schema
                )

        return await self.validate(
            bundle,
            vectors,
            source_classes=source_classes,
            smoke_samples=smoke_samples,
            smoke_limit=smoke_limit,
            known_version=version,
        )

    async def validate(
        self,
        bundle: CorpusReleaseBundle,
        vectors: IndexVectorBatch,
        *,
        source_classes: Mapping[str, str],
        smoke_samples: int = 3,
        smoke_limit: int = 10,
        known_version: str | None = None,
    ) -> IndexValidationReport:
        if smoke_samples <= 0 or smoke_limit <= 0:
            raise ValueError("smoke sample count and result limit must be positive")
        expected = self._expected_points(bundle, vectors, source_classes=source_classes)
        collection = bundle.manifest.content.qdrant_collection
        version = known_version or await self._require_server_version()
        configuration = self._collection_configuration(bundle, vectors)
        collection_info = await self._qdrant.get_collection(collection)
        if collection_info is None:
            raise IndexValidationError(["COLLECTION_NOT_FOUND"])
        self._validate_collection_configuration(
            collection_info, configuration, require_payload_indexes=True
        )
        if collection_info.get("status") != "green":
            raise IndexValidationError(["COLLECTION_NOT_GREEN"])
        if collection_info.get("optimizer_status") != "ok":
            raise IndexValidationError(["COLLECTION_OPTIMIZER_NOT_OK"])
        if collection_info.get("points_count") != len(expected):
            raise IndexValidationError(["COLLECTION_POINT_COUNT_MISMATCH"])

        actual = await self._qdrant.scroll_points(collection, maximum=len(expected) + 1)
        self._validate_points(actual, expected, vectors, require_complete=True)
        smoke_results = await self._smoke_tests(
            collection,
            expected,
            vectors,
            sample_count=min(smoke_samples, len(expected)),
            result_limit=min(smoke_limit, len(expected)),
        )
        ordered = [expected[key] for key in sorted(expected)]
        evidence = {item.evidence_id: item for item in bundle.evidence}
        content = IndexValidationReportContent(
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            bundle_sha256=canonical_sha256(bundle),
            qdrant_collection=collection,
            qdrant_version=version,
            collection_config_sha256=canonical_sha256(
                {**configuration, "payload_indexes": PAYLOAD_INDEXES}
            ),
            vector_batch_sha256=vectors.batch_sha256,
            point_id_set_sha256=canonical_sha256(
                {"points": [(item.evidence_id, item.point_id) for item in ordered]}
            ),
            evidence_set_sha256=canonical_sha256(
                {
                    "evidence": [
                        (item.evidence_id, evidence[item.evidence_id].sha256)
                        for item in ordered
                    ]
                }
            ),
            payload_set_sha256=canonical_sha256(
                {
                    "payloads": [
                        (item.evidence_id, item.payload["payload_sha256"])
                        for item in ordered
                    ]
                }
            ),
            vector_set_sha256=canonical_sha256(
                {
                    "vectors": [
                        (item.evidence_id, item.vector_sha256) for item in ordered
                    ]
                }
            ),
            point_count=len(ordered),
            dense=vectors.content.dense,
            sparse=vectors.content.sparse,
            smoke_results=tuple(smoke_results),
            validated_at=utc_now(),
        )
        return IndexValidationReport.seal(content)

    async def _require_server_version(self) -> str:
        version = await self._qdrant.server_version()
        if version != self._expected_qdrant_version:
            raise IndexValidationError(
                [f"QDRANT_VERSION_MISMATCH:{version}:{self._expected_qdrant_version}"]
            )
        return version

    @staticmethod
    def _expected_points(
        bundle: CorpusReleaseBundle,
        vectors: IndexVectorBatch,
        *,
        source_classes: Mapping[str, str],
    ) -> dict[str, _ExpectedPoint]:
        content = bundle.manifest.content
        if vectors.content.corpus_release_id != content.corpus_release_id:
            raise IndexValidationError(["VECTOR_BATCH_RELEASE_MISMATCH"])
        if vectors.content.manifest_sha256 != bundle.manifest.manifest_sha256:
            raise IndexValidationError(["VECTOR_BATCH_MANIFEST_MISMATCH"])
        evidence = {item.evidence_id: item for item in bundle.evidence}
        vector_records = {item.evidence_id: item for item in vectors.content.records}
        if set(evidence) != set(vector_records):
            raise IndexValidationError(["VECTOR_BATCH_EVIDENCE_SET_MISMATCH"])
        if set(source_classes) != {item.source_id for item in bundle.evidence}:
            raise IndexValidationError(["SOURCE_CLASS_SET_MISMATCH"])

        expected: dict[str, _ExpectedPoint] = {}
        for evidence_id in sorted(evidence):
            record = evidence[evidence_id]
            vector = vector_records[evidence_id]
            if (
                record.verification.approval_status
                is not EvidenceApprovalStatus.APPROVED
            ):
                raise IndexValidationError([f"UNAPPROVED_EVIDENCE:{evidence_id}"])
            if vector.evidence_sha256 != record.sha256:
                raise IndexValidationError([f"VECTOR_EVIDENCE_DIGEST_MISMATCH:{evidence_id}"])
            vector_sha256 = _vector_sha256(vector)
            dense_values = tuple(_float32(item) for item in vector.dense)
            if (
                vectors.content.dense.distance == "Cosine"
                and math.sqrt(sum(item * item for item in dense_values)) == 0
            ):
                raise IndexValidationError([f"DENSE_ZERO_VECTOR:{evidence_id}"])
            source_class = source_classes[record.source_id]
            if not isinstance(source_class, str) or not source_class.strip():
                raise IndexValidationError([f"SOURCE_CLASS_INVALID:{record.source_id}"])
            payload_without_digest = QdrantIndexService._point_payload(
                record,
                bundle,
                source_class=source_class,
                vector_sha256=vector_sha256,
            )
            payload = {
                **payload_without_digest,
                "payload_sha256": canonical_sha256(payload_without_digest),
            }
            point_id = stable_qdrant_point_id(evidence_id)
            if point_id in expected:
                raise IndexValidationError([f"POINT_ID_COLLISION:{evidence_id}"])
            expected[point_id] = _ExpectedPoint(
                evidence_id=evidence_id,
                point_id=point_id,
                payload=payload,
                dense=dense_values,
                sparse_indices=vector.sparse.indices,
                sparse_values=tuple(_float32(item) for item in vector.sparse.values),
                vector_sha256=vector_sha256,
            )
        return expected

    @staticmethod
    def _point_payload(
        evidence: CorpusEvidenceRecord,
        bundle: CorpusReleaseBundle,
        *,
        source_class: str,
        vector_sha256: str,
    ) -> dict[str, Any]:
        return {
            "index_schema_version": INDEX_CONTRACT_VERSION,
            "point_id_scheme": POINT_ID_SCHEME,
            "corpus_release_id": evidence.corpus_release_id,
            "manifest_sha256": bundle.manifest.manifest_sha256,
            "evidence_id": evidence.evidence_id,
            "evidence_sha256": evidence.sha256,
            "vector_sha256": vector_sha256,
            "content_search_sha256": hashlib.sha256(
                evidence.content_search.encode("utf-8")
            ).hexdigest(),
            "source_id": evidence.source_id,
            "source_class": source_class,
            "source_version_id": evidence.source_version_id,
            "source_artifact_sha256": evidence.source_artifact_sha256,
            "publisher_id": evidence.publisher_id,
            "jurisdiction": evidence.jurisdiction,
            "language": evidence.language,
            "lifecycle_status": evidence.lifecycle_status.value,
            "approval_status": evidence.verification.approval_status.value,
            "evidence_roles": [item.value for item in evidence.evidence_roles],
            "applicability": evidence.applicability.model_dump(mode="json"),
            "recommendation_grade": (
                evidence.recommendation_grade.model_dump(mode="json")
                if evidence.recommendation_grade is not None
                else None
            ),
            "render_allowed": evidence.render_allowed,
        }

    @staticmethod
    def _collection_configuration(
        bundle: CorpusReleaseBundle, vectors: IndexVectorBatch
    ) -> dict[str, Any]:
        dense = vectors.content.dense
        sparse = vectors.content.sparse
        return {
            "vectors": {
                dense.name: {"size": dense.dimension, "distance": dense.distance}
            },
            "sparse_vectors": {sparse.name: {"modifier": sparse.modifier}},
            "metadata": {
                "index_schema_version": INDEX_CONTRACT_VERSION,
                "corpus_release_id": bundle.manifest.content.corpus_release_id,
                "manifest_sha256": bundle.manifest.manifest_sha256,
                "bundle_sha256": canonical_sha256(bundle),
                "vector_batch_sha256": vectors.batch_sha256,
                "dense_model_artifact_sha256": dense.model.artifact_sha256,
                "sparse_model_artifact_sha256": sparse.model.artifact_sha256,
                "point_id_scheme": POINT_ID_SCHEME,
            },
        }

    @staticmethod
    def _validate_collection_configuration(
        actual: Mapping[str, Any],
        expected: Mapping[str, Any],
        *,
        require_payload_indexes: bool,
    ) -> None:
        blockers: list[str] = []
        config = actual.get("config")
        if not isinstance(config, dict):
            raise IndexValidationError(["COLLECTION_CONFIG_MALFORMED"])
        params = config.get("params")
        if not isinstance(params, dict):
            raise IndexValidationError(["COLLECTION_PARAMS_MALFORMED"])
        if params.get("vectors") != expected["vectors"]:
            blockers.append("DENSE_VECTOR_CONFIG_MISMATCH")
        if params.get("sparse_vectors") != expected["sparse_vectors"]:
            blockers.append("SPARSE_VECTOR_CONFIG_MISMATCH")
        if config.get("metadata") != expected["metadata"]:
            blockers.append("COLLECTION_METADATA_MISMATCH")

        payload_schema = actual.get("payload_schema")
        if not isinstance(payload_schema, dict):
            blockers.append("COLLECTION_PAYLOAD_SCHEMA_MALFORMED")
        else:
            unexpected = set(payload_schema) - set(PAYLOAD_INDEXES)
            blockers.extend(f"UNEXPECTED_PAYLOAD_INDEX:{item}" for item in sorted(unexpected))
            if require_payload_indexes:
                for field_name, field_type in PAYLOAD_INDEXES.items():
                    definition = payload_schema.get(field_name)
                    if not isinstance(definition, dict):
                        blockers.append(f"PAYLOAD_INDEX_MISSING:{field_name}")
                    elif definition.get("data_type") != field_type:
                        blockers.append(f"PAYLOAD_INDEX_TYPE_MISMATCH:{field_name}")
        if blockers:
            raise IndexValidationError(blockers)

    @staticmethod
    def _validate_points(
        actual_points: list[dict[str, Any]],
        expected: Mapping[str, _ExpectedPoint],
        vectors: IndexVectorBatch,
        *,
        require_complete: bool,
    ) -> set[str]:
        blockers: list[str] = []
        actual_by_id: dict[str, dict[str, Any]] = {}
        for point in actual_points:
            point_id = point.get("id")
            if not isinstance(point_id, str):
                blockers.append("POINT_ID_NOT_UUID_STRING")
                continue
            if point_id in actual_by_id:
                blockers.append(f"DUPLICATE_POINT_ID:{point_id}")
                continue
            actual_by_id[point_id] = point
        unexpected = set(actual_by_id) - set(expected)
        blockers.extend(f"UNEXPECTED_POINT:{item}" for item in sorted(unexpected))
        if require_complete:
            missing = set(expected) - set(actual_by_id)
            blockers.extend(f"MISSING_POINT:{item}" for item in sorted(missing))

        for point_id in sorted(set(actual_by_id) & set(expected)):
            point = actual_by_id[point_id]
            wanted = expected[point_id]
            if point.get("payload") != wanted.payload:
                blockers.append(f"POINT_PAYLOAD_MISMATCH:{wanted.evidence_id}")
            vector = point.get("vector")
            if not isinstance(vector, dict) or set(vector) != {
                vectors.content.dense.name,
                vectors.content.sparse.name,
            }:
                blockers.append(f"POINT_VECTOR_NAMES_MISMATCH:{wanted.evidence_id}")
                continue
            actual_dense = vector.get(vectors.content.dense.name)
            actual_sparse = vector.get(vectors.content.sparse.name)
            expected_dense = wanted.dense
            if vectors.content.dense.distance == "Cosine":
                norm = math.sqrt(sum(value * value for value in expected_dense))
                if norm == 0:
                    blockers.append(f"DENSE_ZERO_VECTOR:{wanted.evidence_id}")
                    expected_dense = ()
                else:
                    expected_dense = tuple(value / norm for value in expected_dense)
            if not QdrantIndexService._close_vector(actual_dense, expected_dense):
                blockers.append(f"DENSE_VECTOR_MISMATCH:{wanted.evidence_id}")
            if not isinstance(actual_sparse, dict):
                blockers.append(f"SPARSE_VECTOR_MALFORMED:{wanted.evidence_id}")
            else:
                if actual_sparse.get("indices") != list(wanted.sparse_indices):
                    blockers.append(f"SPARSE_INDICES_MISMATCH:{wanted.evidence_id}")
                if not QdrantIndexService._close_vector(
                    actual_sparse.get("values"), wanted.sparse_values
                ):
                    blockers.append(f"SPARSE_VALUES_MISMATCH:{wanted.evidence_id}")
        if blockers:
            raise IndexValidationError(blockers)
        return set(actual_by_id)

    @staticmethod
    def _close_vector(actual: object, expected: tuple[float, ...]) -> bool:
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            isinstance(left, (int, float))
            and math.isfinite(float(left))
            and math.isclose(float(left), right, rel_tol=1e-5, abs_tol=1e-6)
            for left, right in zip(actual, expected, strict=True)
        )

    async def _smoke_tests(
        self,
        collection: str,
        expected: Mapping[str, _ExpectedPoint],
        vectors: IndexVectorBatch,
        *,
        sample_count: int,
        result_limit: int,
    ) -> list[RetrievalSmokeResult]:
        selected = [expected[key] for key in sorted(expected)[:sample_count]]
        results: list[RetrievalSmokeResult] = []
        release_filter = {
            "must": [
                {
                    "key": "corpus_release_id",
                    "match": {"value": vectors.content.corpus_release_id},
                },
                {"key": "approval_status", "match": {"value": "APPROVED"}},
            ]
        }
        for point in selected:
            common = {
                "filter": release_filter,
                "limit": result_limit,
                "with_payload": ["evidence_id", "evidence_sha256"],
                "with_vector": False,
            }
            dense = await self._qdrant.query_points(
                collection,
                {
                    **common,
                    "query": list(point.dense),
                    "using": vectors.content.dense.name,
                },
            )
            sparse = await self._qdrant.query_points(
                collection,
                {
                    **common,
                    "query": {
                        "indices": list(point.sparse_indices),
                        "values": list(point.sparse_values),
                    },
                    "using": vectors.content.sparse.name,
                },
            )
            dense_rank = self._result_rank(dense, point)
            sparse_rank = self._result_rank(sparse, point)
            if dense_rank is None or sparse_rank is None:
                missing = "DENSE" if dense_rank is None else "SPARSE"
                if dense_rank is None and sparse_rank is None:
                    missing = "DENSE_AND_SPARSE"
                raise IndexValidationError(
                    [f"{missing}_SMOKE_TEST_FAILED:{point.evidence_id}"]
                )
            results.append(
                RetrievalSmokeResult(
                    evidence_id=point.evidence_id,
                    point_id=point.point_id,
                    dense_rank=dense_rank,
                    sparse_rank=sparse_rank,
                    result_limit=result_limit,
                )
            )
        return results

    @staticmethod
    def _result_rank(
        results: list[dict[str, Any]], expected: _ExpectedPoint
    ) -> int | None:
        for rank, result in enumerate(results, start=1):
            if result.get("id") != expected.point_id:
                continue
            payload = result.get("payload")
            if not isinstance(payload, dict):
                return None
            if (
                payload.get("evidence_id") != expected.evidence_id
                or payload.get("evidence_sha256")
                != expected.payload["evidence_sha256"]
            ):
                return None
            return rank
        return None
