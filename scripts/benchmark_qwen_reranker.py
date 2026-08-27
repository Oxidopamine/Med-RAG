"""Time bounded Qwen3 reranker settings on the longest release passages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

from app.corpus_steward.reranking import _Qwen3TransformersRerankerRuntime

DEFAULT_INSTRUCTION = (
    "Given a clinical evidence retrieval query, retrieve relevant guideline passages "
    "that answer the query."
)
DEFAULT_QUERY = (
    "When should antiretroviral therapy be offered, avoided, dosed, and monitored?"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--setting", action="append", required=True, help="MAX_LENGTH:BATCH_SIZE")
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--dtype", choices=("float32", "int8_dynamic"), default="float32")
    arguments = parser.parse_args()
    if arguments.sample_size <= 0:
        parser.error("--sample-size must be positive")
    raw = json.loads(arguments.bundle.read_text(encoding="utf-8"))
    documents = sorted(
        (item["content_exact"] for item in raw["evidence"]),
        key=len,
        reverse=True,
    )[: arguments.sample_size]
    pairs = tuple((DEFAULT_QUERY, document) for document in documents)
    runtime = _Qwen3TransformersRerankerRuntime(
        arguments.model_root,
        device="cpu",
        dtype=arguments.dtype,
    )
    for setting in arguments.setting:
        try:
            maximum, batch_size = (int(item) for item in setting.split(":", maxsplit=1))
        except ValueError:
            parser.error(f"invalid --setting: {setting}")
        started = perf_counter()
        scores = []
        for offset in range(0, len(pairs), batch_size):
            scores.extend(
                runtime.score(
                    pairs[offset : offset + batch_size],
                    instruction=DEFAULT_INSTRUCTION,
                    max_length=maximum,
                )
            )
        print(
            json.dumps(
                {
                    "batch_size": batch_size,
                    "count": len(scores),
                    "dtype": arguments.dtype,
                    "max_length": maximum,
                    "seconds": round(perf_counter() - started, 3),
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
