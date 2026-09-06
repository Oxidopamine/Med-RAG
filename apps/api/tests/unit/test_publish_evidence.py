"""The publisher's redaction rule, checked on synthetic documents and on the published file.

Two obligations live in `scripts/publish_evidence.py`. The licensing one, that no WHO
passage text is published, is asserted in `test_stated_figures.py`. The measurement one,
added by the correctness measurement plan (Section 2.2, item 3), is asserted here: a bound
on `p_wrong` is withheld from any published run whose every `bucket` is null, because a
bound over an unclassified run reads as a safety figure and is not one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
RESULTS = REPO_ROOT / "benchmarks" / "results"


def _load(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


publisher = _load("publish_evidence")


def _document(*, bucket: str | None) -> dict:
    return {
        "results": [
            {
                "question_id": "Q1",
                "bucket": bucket,
                "retrieval": {
                    "passages": [{"evidence_id": "EV_1", "rendered_text": "WHO text",
                                  "rendered_text_truncated": False}]
                },
                "generation": {
                    "abstained": False,
                    "claims": [{"text": "c", "evidence_ids": ["EV_1"]}],
                },
            }
        ],
        "summary": {
            "p_wrong_zero_occurrence_upper_bound_95": 0.95,
            "p_wrong_zero_occurrence_upper_bound_95_questions": 0.95,
            "p_wrong_zero_occurrence_upper_bound_95_answered": 0.95,
        },
    }


def test_redaction_withholds_every_p_wrong_bound_when_no_bucket_exists():
    document, removed = publisher.redact_coverage(_document(bucket=None))
    assert removed == 2
    for key in publisher.P_WRONG_KEYS:
        assert document["summary"][key] is None, key
    assert document["_redaction"]["p_wrong_bound_withheld"] == publisher.P_WRONG_WITHHELD
    assert document["_redaction"]["p_wrong_keys_nulled"] == list(publisher.P_WRONG_KEYS)
    assert "rendered_text" not in document["results"][0]["retrieval"]["passages"][0]


def test_redaction_keeps_the_bound_on_a_classified_run():
    document, _ = publisher.redact_coverage(_document(bucket="ANSWERED_CORRECT"))
    assert document["summary"]["p_wrong_zero_occurrence_upper_bound_95"] == 0.95
    assert "p_wrong_bound_withheld" not in document["_redaction"]


def test_redaction_of_a_run_that_retrieves_nothing_removes_nothing():
    """The closed-book arm has no passages, so its redaction count is legitimately zero."""

    document = {"results": [{"question_id": "Q1", "bucket": None, "retrieval": None,
                             "generation": {"abstained": False, "claims": []}}],
                "summary": {}}
    redacted, removed = publisher.redact_coverage(document)
    assert removed == 0
    assert redacted["_redaction"]["p_wrong_keys_nulled"] == []


@pytest.mark.skipif(not RESULTS.exists(), reason="published evidence is not present")
def test_the_published_stage2_run_withholds_its_bound():
    """`p_wrong_zero_occurrence_upper_bound_95` was 0.0181 in the shipped file over an
    unclassified run; it is now null with the reason recorded beside the redaction."""

    document = json.loads(
        (RESULTS / "coverage-stage2-gemini-3.7-flash.json").read_text(encoding="utf-8")
    )
    assert all(result["bucket"] is None for result in document["results"])
    assert document["summary"]["p_wrong_zero_occurrence_upper_bound_95"] is None
    assert document["_redaction"]["p_wrong_bound_withheld"] == publisher.P_WRONG_WITHHELD


def test_the_four_planned_runs_are_named_as_the_plan_names_them():
    sources = [source for source, _, _ in publisher.PLANNED_COVERAGE_RUNS]
    assert sources == [
        "cm/production-a.json",
        "cm/production-b.json",
        "cm/naive-164.json",
        "cm/closed-book-164.json",
    ]
