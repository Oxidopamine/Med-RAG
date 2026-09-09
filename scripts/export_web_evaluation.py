"""Write the figures the web Evaluation page shows, from the released benchmark files.

The page renders a committed JSON file rather than importing the benchmark artifacts at
build time, so the web build stays inside apps/web and the figures shown are the ones
this script copied from the release, with the source digests beside them.

    python scripts/export_web_evaluation.py

Rewrites apps/web/lib/generated/evaluation.json. Idempotent.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results"
ANALYSIS = ROOT / "benchmarks" / "analysis"
OUTPUT = ROOT / "apps" / "web" / "lib" / "generated" / "evaluation.json"

ARMS = [
    ("production_a", "coverage-production-a.json", "Production, replicate A"),
    ("production_b", "coverage-production-b.json", "Production, replicate B"),
    ("naive", "coverage-naive-164.json", "Naive retrieval baseline"),
    ("closed_book", "coverage-closed-book-164.json", "Closed book (no retrieval)"),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    arms = []
    sources = {}
    for key, filename, label in ARMS:
        path = RESULTS / filename
        data = load(path)
        summary = data["summary"]
        arms.append(
            {
                "key": key,
                "label": label,
                "run_kind": data.get("run_kind"),
                "questions": summary["questions_run"],
                "answered": summary["answered"],
                "abstained": summary["abstained"],
                "answered_rate": summary["answered_rate"],
                "gate_passed": summary.get("gate_passed"),
                "gate_passed_rate": summary.get("gate_passed_rate"),
                "gate_passed_wilson_95": summary.get("gate_passed_wilson_95"),
                "error_records": summary.get("error_records", 0),
            }
        )
        sources[filename] = sha256(path)

    stats_path = ANALYSIS / "cm-statistics-horizon1.json"
    stats = load(stats_path)
    section = stats["sections"]["3.5"]
    sources[stats_path.name] = sha256(stats_path)

    mde_path = ANALYSIS / "mde.json"
    mde = load(mde_path)
    sources[mde_path.name] = sha256(mde_path)

    overlap_path = ANALYSIS / "retrieval-overlap-production-a.json"
    overlap = load(overlap_path)
    sources[overlap_path.name] = sha256(overlap_path)

    payload = {
        "schema_version": 1,
        "generated_by": "scripts/export_web_evaluation.py",
        "statistics_generated_at": stats.get("generated_at"),
        "seed": stats.get("seed"),
        "bootstrap_resamples": stats.get("bootstrap_resamples"),
        "software": stats.get("software"),
        "arms": arms,
        "noise_floor": section["noise_floor"],
        "negative_control_a_versus_b": section["negative_control_a_versus_b"],
        "production_versus_naive": section["q5_production_a_versus_naive"],
        "stage2_versus_a_upper_bound": section["stage2_versus_a_upper_bound"],
        "error_guards": section["error_guards"],
        "mde": {
            "n": mde["mde"]["n"],
            "alpha": mde["mde"]["alpha"],
            "power_target": mde["mde"]["power_target"],
            "anchor": mde["mde"]["corrected"]["anchor"],
            "power_by_delta": mde["mde"]["corrected"]["primary"]["power_by_delta"],
            "mde_at_80_percent": mde["mde"]["corrected"]["primary"]["mde_at_80_percent"],
        },
        "retrieval_overlap": {
            "questions": overlap["questions"],
            "slots": overlap["slots"],
            "distinct_evidence_ids": overlap["distinct_evidence_ids"],
            "recurring_slot_share": overlap["recurring_slot_share"],
            "pair_sharing_rate": overlap["pair_sharing_rate"],
        },
        "sources": sources,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
