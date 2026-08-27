"""Export the pinned local Qwen3 encoder to a CPU/int8 OpenVINO IR.

The source and destination must be explicit local paths. The script never downloads a
model and refuses to overwrite a non-empty destination. Seal the resulting directory with
``corpus-steward model-artifact-manifest`` before it can be used by the runtime.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from importlib import metadata
from pathlib import Path


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError as error:
        raise RuntimeError(
            "install the project 'cpu-optimized' optional dependencies before export"
        ) from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parameters-output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=8192)
    parser.add_argument("--query-instruction", required=True)
    arguments = parser.parse_args()

    source = arguments.source.resolve()
    output = arguments.output.resolve()
    if not source.is_dir():
        raise RuntimeError("the pinned local source model directory is missing")
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("the OpenVINO output directory must not exist or must be empty")
    if arguments.batch_size <= 0 or arguments.max_length <= 0:
        raise RuntimeError("batch size and maximum length must be positive")

    versions = {
        "openvino_version": _version("openvino"),
        "optimum_intel_version": _version("optimum-intel"),
        "nncf_version": _version("nncf"),
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "optimum.commands.optimum_cli",
            "export",
            "openvino",
            "--model",
            str(source),
            "--task",
            "feature-extraction",
            "--weight-format",
            "int8",
            "--library",
            "transformers",
            str(output),
        ],
        check=True,
    )
    parameters = {
        "pooling": "last_token",
        "normalize": True,
        "query_instruction": arguments.query_instruction,
        "document_prefix": "",
        "padding_side": "left",
        "max_length": arguments.max_length,
        "batch_size": arguments.batch_size,
        "device": "CPU",
        "dtype": "int8",
        "weight_format": "int8",
        **versions,
    }
    arguments.parameters_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.parameters_output.write_text(
        json.dumps(parameters, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output), **versions}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
