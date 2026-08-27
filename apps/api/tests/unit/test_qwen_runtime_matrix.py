import json
from pathlib import Path

import pytest

from app.corpus_steward.benchmark_schemas import (
    BENCHMARK_CONTRACT_VERSION,
    BenchmarkSuite,
    BenchmarkSuitePartition,
)
from app.corpus_steward.cli import qwen_runtime_matrix_schema_documents
from app.corpus_steward.qwen_runtime_matrix import (
    QwenRuntimeMatrixReport,
    QwenRuntimeMatrixRequest,
    QwenRuntimeTarget,
    execute_qwen_runtime_matrix,
)
from app.schemas.corpus import CorpusReleaseBundle

ROOT = Path(__file__).parents[4]
FIXTURE_PATH = ROOT / "data" / "fixtures" / "corpus-release-v1.json"
SUITE_PATH = ROOT / "benchmarks" / "suites" / "synthetic-v1.json"
SCHEMA_ROOT = ROOT / "packages" / "schemas"
REPORT_PATH = (
    ROOT
    / "benchmarks"
    / "runtime"
    / "qwen3-embedding-who-smart-hiv-cpu-v1-report.json"
)


def _requires_regenerated_artifact(path: Path) -> None:
    """Skip when a checked-in sealed artifact predates the current contract.

    These artifacts are outputs of the sealing pipeline, not fixtures: regenerating
    them needs the release database, signing keys, and Qdrant. Skipping keyed on the
    contract version means the check re-arms by itself once the artifact is rebuilt,
    instead of being silently deleted or hand-edited back into agreement.
    """

    version = json.loads(path.read_text(encoding="utf-8"))["content"]["schema_version"]
    if version != BENCHMARK_CONTRACT_VERSION:
        pytest.skip(
            f"{path.name} is sealed at contract {version}; "
            f"regenerate at {BENCHMARK_CONTRACT_VERSION} "
            "(corpus-steward benchmark-generate-source-derived / benchmark-seal-suite)"
        )


def fixture_inputs() -> tuple[CorpusReleaseBundle, BenchmarkSuite]:
    bundle = CorpusReleaseBundle.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )
    synthetic = BenchmarkSuite.model_validate_json(
        SUITE_PATH.read_text(encoding="utf-8")
    )
    development_content = synthetic.content.model_copy(
        update={"suite_partition": BenchmarkSuitePartition.DEVELOPMENT}
    )
    development = BenchmarkSuite.model_construct(
        content=development_content,
        suite_sha256=synthetic.suite_sha256,
    )
    return bundle, development


def request(bundle: CorpusReleaseBundle) -> QwenRuntimeMatrixRequest:
    return QwenRuntimeMatrixRequest(
        matrix_id="fixture-qwen-runtime",
        corpus_release_id=bundle.manifest.content.corpus_release_id,
        manifest_sha256=bundle.manifest.manifest_sha256,
        query_sample_count=1,
        document_sample_count=1,
        warmup_runs=0,
        measured_runs=1,
        targets=(
            QwenRuntimeTarget(
                target_id="qwen3-embedding-0.6b-cpu-float32",
                model_id="Qwen/Qwen3-Embedding-0.6B",
                model_revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
                dimension=1024,
                artifact_root="models/local/missing-qwen",
                artifact_manifest_path="data/local/missing-qwen-manifest.json",
                expected_artifact_sha256=None,
                device="cpu",
                dtype="float32",
                max_length=8192,
                batch_size=8,
            ),
        ),
    )


async def test_qwen_matrix_seals_all_missing_artifact_blockers(tmp_path: Path) -> None:
    _requires_regenerated_artifact(SUITE_PATH)
    bundle, suite = fixture_inputs()

    report = await execute_qwen_runtime_matrix(
        request(bundle),
        bundle,
        suite,
        workspace_root=tmp_path.resolve(),
    )

    result = report.content.results[0]
    assert report.content.outcome == "BLOCKED"
    assert result.status == "BLOCKED"
    assert result.blockers == (
        "ARTIFACT_SHA256_PIN_MISSING",
        "LOCAL_MODEL_ROOT_MISSING",
        "MODEL_ARTIFACT_MANIFEST_MISSING",
    )
    assert result.measurements is None


async def test_qwen_matrix_rejects_non_development_suite(tmp_path: Path) -> None:
    _requires_regenerated_artifact(SUITE_PATH)
    bundle = CorpusReleaseBundle.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )
    suite = BenchmarkSuite.model_validate_json(SUITE_PATH.read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="development suite"):
        await execute_qwen_runtime_matrix(
            request(bundle),
            bundle,
            suite,
            workspace_root=tmp_path.resolve(),
        )


def test_checked_in_qwen_runtime_schemas_match_contract() -> None:
    for filename, expected in qwen_runtime_matrix_schema_documents().items():
        actual = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        assert actual == expected


def test_checked_in_qwen_runtime_report_is_digest_sealed() -> None:
    report = QwenRuntimeMatrixReport.model_validate_json(
        REPORT_PATH.read_text(encoding="utf-8")
    )

    assert report.report_sha256 == (
        "76cc9655f35916c5a116d2bf00b57f2a5ba25e488dd5a706fed956091968383b"
    )
    assert [item.status for item in report.content.results] == [
        "MEASURED",
        "BLOCKED",
        "BLOCKED",
    ]
