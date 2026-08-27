"""Cloud Run Job entrypoint: pin a dense embedding artifact, then run the
existing `corpus-steward qwen-runtime-matrix` command against it on GPU.

One image serves every allowlisted dense family — Qwen3-Embedding and BGE-M3
today — because the family, the model, and every runtime setting are env-var
driven, so distinct targets are distinct Cloud Run Job executions rather than
distinct images. The adapter identities, the BGE-M3 revision pin, and the
parameter schemas are imported from `app.corpus_steward.embedding_adapters`
rather than restated here, so this job cannot seal a manifest the matrix would
then refuse to load. Inputs/outputs accept either gs:// URIs or local paths so
the same entrypoint also runs under plain `docker run` for local testing.

Required env vars:
  MODEL_ID              e.g. "Qwen/Qwen3-Embedding-4B" or "BAAI/bge-m3"
  DIMENSION             e.g. "2560" (Qwen3 4B), "4096" (8B), "1024" (BGE-M3)
  INPUT_BUNDLE_URI       corpus release bundle JSON (gs:// or local path)
  INPUT_SUITE_URI        development benchmark suite JSON (gs:// or local path)
  OUTPUT_REPORT_URI      where to write the sealed runtime-matrix report
  MATRIX_ID             e.g. "bge-m3-gpu-fp16-v1"

Optional env vars (sensible GPU defaults shown):
  MODEL_FAMILY            "qwen3" (default) or "bge-m3"; selects the adapter
                           identity and the parameter schema sealed into the
                           manifest
  MODEL_REVISION          40-hex commit SHA. A family carrying a pinned
                           revision (BGE-M3) defaults to that pin and rejects
                           a different one; Qwen3 resolves the HF "main"
                           branch when this is omitted (never guessed)
  POOLING                 defaults to the family's own pooling: "last_token"
                           for Qwen3, "cls" for BGE-M3
  TARGET_ID               defaults to MATRIX_ID
  DEVICE                  default "cuda"
  DTYPE                   default "float16"
  MAX_LENGTH              default "8192" (BGE-M3's schema ceiling)
  BATCH_SIZE              default "16"
  QUERY_SAMPLE_COUNT      default "32"
  DOCUMENT_SAMPLE_COUNT   default "32"
  WARMUP_RUNS             default "1"
  MEASURED_RUNS           default "3"
  QUERY_INSTRUCTION       qwen3 only; default: the release's standard
                           retrieval instruction
  QUERY_PREFIX            bge-m3 only; default "" (BAAI ships bge-m3 without
                           a query instruction, unlike bge-large-en)
  DOCUMENT_PREFIX         bge-m3 only; default ""
  HF_TOKEN                only needed for gated HF repos

`normalize` is sealed True for both families: Qwen3's schema admits nothing
else, and the matrix's `maximum_norm_deviation` check is only meaningful over
unit vectors.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_ROOT = Path("/workspace/run")
DEFAULT_QUERY_INSTRUCTION = (
    "Given a clinical evidence retrieval query, retrieve relevant guideline "
    "passages that answer the query."
)
QWEN3_FAMILY = "qwen3"
BGE_M3_FAMILY = "bge-m3"


@dataclass(frozen=True)
class _DenseFamily:
    """One allowlisted dense adapter identity and the parameter shape it seals."""

    name: str
    adapter_id: str
    adapter_revision: str
    parameter_model: type
    pinned_model_revision: str | None
    pooling: str


def _load_family(name: str) -> _DenseFamily:
    from app.corpus_steward.embedding_adapters import (
        BGE_M3_ADAPTER_ID,
        BGE_M3_ADAPTER_REVISION,
        BGE_M3_MODEL_REVISION,
        QWEN3_ADAPTER_ID,
        QWEN3_ADAPTER_REVISION,
        BGEM3AdapterParameters,
        Qwen3EmbeddingAdapterParameters,
    )

    if name == QWEN3_FAMILY:
        return _DenseFamily(
            name=name,
            adapter_id=QWEN3_ADAPTER_ID,
            adapter_revision=QWEN3_ADAPTER_REVISION,
            parameter_model=Qwen3EmbeddingAdapterParameters,
            pinned_model_revision=None,
            pooling="last_token",
        )
    if name == BGE_M3_FAMILY:
        return _DenseFamily(
            name=name,
            adapter_id=BGE_M3_ADAPTER_ID,
            adapter_revision=BGE_M3_ADAPTER_REVISION,
            parameter_model=BGEM3AdapterParameters,
            # The adapter accepts exactly this revision, so resolving a fresh
            # "main" SHA here would only buy an artifact it refuses to load —
            # after the weights have already been downloaded.
            pinned_model_revision=BGE_M3_MODEL_REVISION,
            pooling="cls",
        )
    raise SystemExit(
        f"unknown MODEL_FAMILY {name!r}: expected {QWEN3_FAMILY} or {BGE_M3_FAMILY}"
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


def _resolve_revision(family: _DenseFamily, model_id: str, explicit: str) -> str:
    pinned = family.pinned_model_revision
    if pinned is not None:
        if explicit and explicit != pinned:
            raise SystemExit(
                f"{family.name} is pinned to revision {pinned}; MODEL_REVISION="
                f"{explicit} would be rejected by the adapter at load time"
            )
        return pinned
    if explicit:
        return explicit
    from huggingface_hub import HfApi

    info = HfApi(token=os.environ.get("HF_TOKEN")).model_info(model_id)
    if not info.sha:
        raise SystemExit(f"could not resolve a commit SHA for {model_id}")
    return info.sha


def _adapter_parameters(
    family: _DenseFamily,
    *,
    pooling: str,
    max_length: int,
    batch_size: int,
    device: str,
    dtype: str,
) -> dict[str, object]:
    """Shape this family's sealed parameters, and reject them before any download."""

    common: dict[str, object] = {
        "pooling": pooling,
        "normalize": True,
        "max_length": max_length,
        "batch_size": batch_size,
        "device": device,
        "dtype": dtype,
    }
    if family.name == BGE_M3_FAMILY:
        parameters = {
            **common,
            "query_prefix": _env("QUERY_PREFIX", ""),
            "document_prefix": _env("DOCUMENT_PREFIX", ""),
        }
    else:
        parameters = {
            **common,
            "query_instruction": _env("QUERY_INSTRUCTION", DEFAULT_QUERY_INSTRUCTION),
            "document_prefix": "",
            "padding_side": "left",
        }
    family.parameter_model.model_validate(parameters)
    return parameters


def _validate_target(target: dict[str, object]) -> None:
    """Reject a bad model/dimension/dtype before a weight download pays for it."""

    from app.corpus_steward.qwen_runtime_matrix import QwenRuntimeTarget

    QwenRuntimeTarget.model_validate(target)


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

    family = _load_family(_env("MODEL_FAMILY", QWEN3_FAMILY))
    target_id = _env("TARGET_ID", matrix_id)
    device = _env("DEVICE", "cuda")
    dtype = _env("DTYPE", "float16")
    pooling = _env("POOLING", family.pooling)
    max_length = int(_env("MAX_LENGTH", "8192"))
    batch_size = int(_env("BATCH_SIZE", "16"))
    query_sample_count = int(_env("QUERY_SAMPLE_COUNT", "32"))
    document_sample_count = int(_env("DOCUMENT_SAMPLE_COUNT", "32"))
    warmup_runs = int(_env("WARMUP_RUNS", "1"))
    measured_runs = int(_env("MEASURED_RUNS", "3"))

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    bundle_path = _download(input_bundle_uri, WORKSPACE_ROOT / "input" / "bundle.json")
    suite_path = _download(input_suite_uri, WORKSPACE_ROOT / "input" / "suite.json")

    parameters = _adapter_parameters(
        family,
        pooling=pooling,
        max_length=max_length,
        batch_size=batch_size,
        device=device,
        dtype=dtype,
    )
    revision = _resolve_revision(family, model_id, _env("MODEL_REVISION"))
    print(
        json.dumps(
            {
                "model_family": family.name,
                "model_id": model_id,
                "resolved_model_revision": revision,
                "adapter_id": family.adapter_id,
            }
        )
    )

    model_root_rel = Path("models") / target_id
    model_root = WORKSPACE_ROOT / model_root_rel
    manifest_rel = Path("manifests") / f"{target_id}.json"
    manifest_path = WORKSPACE_ROOT / manifest_rel
    target: dict[str, object] = {
        "target_id": target_id,
        "model_id": model_id,
        "model_revision": revision,
        "dimension": dimension,
        "artifact_root": str(model_root_rel),
        "artifact_manifest_path": str(manifest_rel),
        "device": device,
        "dtype": dtype,
        "max_length": max_length,
        "batch_size": batch_size,
    }
    _validate_target(target)

    _download_model(model_id, revision, model_root)

    params_path = WORKSPACE_ROOT / "adapter-parameters.json"
    params_path.write_text(json.dumps(parameters), encoding="utf-8")

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
            family.adapter_id,
            "--adapter-revision",
            family.adapter_revision,
            "--adapter-parameters",
            str(params_path),
            "--output",
            str(manifest_path),
        ]
    )
    target["expected_artifact_sha256"] = _last_json_line(manifest_output)[
        "artifact_sha256"
    ]

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
                "targets": [target],
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
