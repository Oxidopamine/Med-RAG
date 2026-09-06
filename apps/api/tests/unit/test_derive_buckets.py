"""`derive_buckets.py` applies Section 4.4 through `cm_statistics.py` and checks the prefixes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _load(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


derive_buckets = _load("derive_buckets")
statistics = _load("cm_statistics")


def _run() -> dict:
    return {
        "results": [
            {
                "question_id": "Q1",
                "generation": {
                    "abstained": False,
                    "claims": [{"text": "a", "evidence_ids": ["EV_1", "EV_2"]}],
                },
            },
            {
                "question_id": "Q2",
                "generation": {
                    "abstained": False,
                    "claims": [{"text": "b", "evidence_ids": ["EV_3"]}],
                },
            },
            {
                "question_id": "Q3",
                "generation": None,
                "gate_reason": "INCOMPLETE_EVIDENCE_ROLE_SET",
            },
            {
                "question_id": "Q4",
                "generation": {"abstained": True, "reason_code": "MODEL_DECLARED_INSUFFICIENT"},
            },
            {
                "question_id": "Q5",
                "generation": {"abstained": True, "reason_code": "MODEL_DECLARED_INSUFFICIENT"},
            },
            {
                "question_id": "Q6",
                "generation": {
                    "error": "429",
                    "error_class": "RESOURCE_EXHAUSTED",
                    "abstained": None,
                },
            },
        ]
    }


def _labels() -> dict:
    return {
        "records": {
            "Q1": {
                "arm": "production",
                "presentation_defect": False,
                "claims": [
                    {
                        "index": 1,
                        "joint_attribution": "ATTRIBUTABLE",
                        "pair_attribution": {"EV_1": "ATTRIBUTABLE", "EV_2": "NO_SUPPORT"},
                        "eligibility_drop": False,
                    },
                ],
            },
            "Q2": {
                "arm": "production",
                "presentation_defect": False,
                "claims": [
                    {
                        "index": 1,
                        "joint_attribution": "CONTRADICTORY",
                        "pair_attribution": {"EV_3": "CONTRADICTORY"},
                        "eligibility_drop": False,
                    },
                ],
            },
        },
        "gate_blocked": {"Q3": {"gate_right": False, "dak_has_answer": True, "note": ""}},
        "abstained_sample": {
            "size": 1,
            "seed": 1,
            "Q4": {"any_retrieved_passage_answers": False, "question_valid": True},
        },
    }


def test_buckets_follow_section_4_4_and_withhold_the_judge_cells_below_the_floor():
    oracle = {"shortlist_size": 25, "records": {"Q5": {"kind": "abstained", "verdict": "PRESENT"}}}
    result = derive_buckets.derive(_run(), _labels(), oracle, statistics=statistics)
    assert result["oracle_floor_cleared"] is False
    assert result["buckets"] == {
        "Q1": "ANSWERED_DEFECTIVE",
        "Q2": "ANSWERED_WRONG",
        "Q3": "ABSTAINED_AVOIDABLE",
        "Q4": "ABSTAINED_CORRECT",
        "Q5": None,
        "Q6": None,
    }
    assert result["unclassified"] == ["Q5", "Q6"]
    assert result["prefix_problems"] == []
    assert result["unlabelled_answered_records"] == []


def test_a_cleared_oracle_floor_fills_the_unread_abstentions_from_the_judge():
    oracle = {
        "shortlist_size": 25,
        "records": {
            "Q5": {"kind": "abstained", "verdict": "PRESENT"},
            **{
                f"C{i}": {"kind": "answered_control", "cited_in_pre_union_rankings": True}
                for i in range(10)
            },
        },
    }
    result = derive_buckets.derive(_run(), _labels(), oracle, statistics=statistics)
    assert result["oracle_floor_cleared"] is True
    assert result["buckets"]["Q5"] == "ABSTAINED_AVOIDABLE"
    assert result["bucket_sources"]["judge"] == 1


def test_an_unlabelled_answered_record_is_reported_as_a_partial_census():
    labels = _labels()
    del labels["records"]["Q2"]
    result = derive_buckets.derive(_run(), labels, None, statistics=statistics)
    assert result["unlabelled_answered_records"] == ["Q2"]
    assert result["buckets"]["Q2"] is None
