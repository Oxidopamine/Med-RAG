"""Cloud Run Job entrypoint: pin a Qwen3 embedding artifact, then run the
existing `corpus-steward qwen-runtime-matrix` command against it on GPU.

Every parameter is env-var driven so the same image serves every target
(4B, 8B, different dtypes) via distinct Cloud Run Job executions rather than
distinct images. Inputs/outputs accept either gs:// URIs or local paths so
the same entrypoint also runs under plain `docker run` for local testing.

Required env vars:
  MODEL_ID              e.g. "Qwen/Qwen3-Embedding-4B"
  DIMENSION             e.g. "2560" (4B) or "4096" (8B)
  INPUT_BUNDLE_URI       corpus release bundle JSON (gs:// or local path)
  INPUT_SUITE_URI        development benchmark suite JSON (gs:// or local path)
  OUTPUT_REPORT_URI      where to write the sealed runtime-matrix report
  MATRIX_ID              e.g. "qwen3-embedding-4b-gpu-fp16-v1"

Optional env vars (sensible GPU defaults shown):
  MODEL_REVISION          40-hex commit SHA; resolved from the HF "main"
                           branch if omitted (never guessed/hardcoded)
  TARGET_ID               defaults to MATRIX_ID
  DEVICE                  default "cuda"
  DTYPE                   default "float16"
  MAX_LENGTH              default "8192"
  BATCH_SIZE              default "16"
  QUERY_SAMPLE_COUNT      default "32"
  DOCUMENT_SAMPLE_COUNT   default "32"
  WARMUP_RUNS             default "1"
  MEASURED_RUNS           default "3"
  QUERY_INSTRUCTION       default: the release's standard retrieval instruction
  HF_TOKEN                only needed for gated HF repos
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WORKSPACE_ROOT = Path("/workspace/run")
DEFAULT_QUERY_INSTRUCTION = (
    "Given a clinical evidence retrieval query, retrieve relevant guideline "
    "passages that answer the query."
)


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"missing required env var: {name}")
    return value or ""


def _is_gcs(uri: str) -> bool:
    return uri.startswith("gs://")


def _download(uri: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _is_gcs(uri):
        from google.cloud import storage

        bucket_name, _, blob_name = uri.removeprefix("gs://").partition("/")
        storage.Client().bucket(bucket_name).blob(blob_name).download_to_filename(
            str(destination)
        )
    else:
        destination.write_bytes(Path(uri).read_bytes())
    return destination


def _upload(source: Path, uri: str) -> None:
    if _is_gcs(uri):
        from google.cloud import storage

        bucket_name, _, blob_name = uri.removeprefix("gs://").partition("/")
        storage.Client().bucket(bucket_name).blob(blob_name).upload_from_filename(
            str(source)
        )
    else:
        Path(uri).parent.mkdir(parents=True, exist_ok=True)
        Path(uri).write_bytes(source.read_bytes())


def _resolve_revision(model_id: str, explicit: str) -> str:
    if explicit:
        return explicit
    from huggingface_hub import HfApi

    info = HfApi(token=os.environ.get("HF_TOKEN")).model_info(model_id)
    if not info.sha:
        raise SystemExit(f"could not resolve a commit SHA for {model_id}")
    return info.sha


def _download_model(model_id: str, revision: str, root: Path) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=model_id,
        revision=revision,
        local_dir=str(root),
        token=os.environ.get("HF_TOKEN"),
    )


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}")
    return result.stdout


def _last_json_line(output: str) -> dict:
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise SystemExit("expected a JSON line on stdout, found none")


def main() -> int:
    model_id = _env("MODEL_ID", required=True)
    dimension = int(_env("DIMENSION", required=True))
    input_bundle_uri = _env("INPUT_BUNDLE_URI", required=True)
    input_suite_uri = _env("INPUT_SUITE_URI", required=True)
    output_report_uri = _env("OUTPUT_REPORT_URI", required=True)
    matrix_id = _env("MATRIX_ID", required=True)

    target_id = _env("TARGET_ID", matrix_id)
    device = _env("DEVICE", "cuda")
    dtype = _env("DTYPE", "float16")
    max_length = int(_env("MAX_LENGTH", "8192"))
    batch_size = int(_env("BATCH_SIZE", "16"))
    query_sample_count = int(_env("QUERY_SAMPLE_COUNT", "32"))
    document_sample_count = int(_env("DOCUMENT_SAMPLE_COUNT", "32"))
    warmup_runs = int(_env("WARMUP_RUNS", "1"))
    measured_runs = int(_env("MEASURED_RUNS", "3"))
    query_instruction = _env("QUERY_INSTRUCTION", DEFAULT_QUERY_INSTRUCTION)

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    bundle_path = _download(input_bundle_uri, WORKSPACE_ROOT / "input" / "bundle.json")
    suite_path = _download(input_suite_uri, WORKSPACE_ROOT / "input" / "suite.json")

    revision = _resolve_revision(model_id, _env("MODEL_REVISION"))
    print(json.dumps({"resolved_model_revision": revision, "model_id": model_id}))

    model_root_rel = Path("models") / target_id
    model_root = WORKSPACE_ROOT / model_root_rel
    _download_model(model_id, revision, model_root)

    params_path = WORKSPACE_ROOT / "adapter-parameters.json"
    params_path.write_text(
        json.dumps(
            {
                "pooling": "last_token",
                "normalize": True,
                "query_instruction": query_instruction,
                "document_prefix": "",
                "padding_side": "left",
                "max_length": max_length,
                "batch_size": batch_size,
                "device": device,
                "dtype": dtype,
            }
        ),
        encoding="utf-8",
    )

    manifest_rel = Path("manifests") / f"{target_id}.json"
    manifest_path = WORKSPACE_ROOT / manifest_rel
    manifest_output = _run(
        [
            "corpus-steward",
            "model-artifact-manifest",
            str(model_root),
            "--kind",
            "DENSE",
            "--model-id",
            model_id,
            "--revision",
            revision,
            "--dimension",
            str(dimension),
            "--adapter-id",
            "med-rag/qwen3-embedding",
            "--adapter-revision",
            "1.0.0",
            "--adapter-parameters",
            str(params_path),
            "--output",
            str(manifest_path),
        ]
    )
    artifact_sha256 = _last_json_line(manifest_output)["artifact_sha256"]

    bundle_raw = json.loads(bundle_path.read_text(encoding="utf-8"))
    suite_raw = json.loads(suite_path.read_text(encoding="utf-8"))

    request_path = WORKSPACE_ROOT / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "matrix_id": matrix_id,
                "corpus_release_id": bundle_raw["manifest"]["content"]["corpus_release_id"],
                "manifest_sha256": bundle_raw["manifest"]["manifest_sha256"],
                "query_sample_count": query_sample_count,
                "document_sample_count": document_sample_count,
                "warmup_runs": warmup_runs,
                "measured_runs": measured_runs,
                "targets": [
                    {
                        "target_id": target_id,
                        "model_id": model_id,
                        "model_revision": revision,
                        "dimension": dimension,
                        "artifact_root": str(model_root_rel),
                        "artifact_manifest_path": str(manifest_rel),
                        "expected_artifact_sha256": artifact_sha256,
                        "device": device,
                        "dtype": dtype,
                        "max_length": max_length,
                        "batch_size": batch_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    del suite_raw  # loaded only to fail fast on a malformed suite file

    report_path = WORKSPACE_ROOT / "report.json"
    _run(
        [
            "corpus-steward",
            "qwen-runtime-matrix",
            str(request_path),
            "--bundle",
            str(bundle_path),
            "--suite",
            str(suite_path),
            "--workspace-root",
            str(WORKSPACE_ROOT),
            "--output",
            str(report_path),
        ]
    )

    _upload(report_path, output_report_uri)
    _upload(manifest_path, output_report_uri.rsplit("/", 1)[0] + f"/{target_id}-manifest.json")
    print(json.dumps({"status": "COMPLETE", "report_uri": output_report_uri}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
