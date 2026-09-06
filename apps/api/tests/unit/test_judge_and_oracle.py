"""The pure parts of the claim judge and the abstention oracle (plan Sections 6 and 7)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))


def _load(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


judge = _load("judge_claims")
oracle = _load("abstention_oracle")
sweep = _load("retrieval_sweep")

CLAIMS = [
    {"text": "Start ART.", "evidence_ids": ["EV_1", "EV_2"]},
    {"text": "Repeat viral load at 6 months.", "evidence_ids": ["EV_3"]},
]


def _texts(evidence_id: str) -> str | None:
    return {"EV_1": "Passage one.", "EV_2": "Passage two.", "EV_3": "Passage three."}.get(
        evidence_id
    )


class TestJudgePrompts:
    def test_two_calls_carry_disjoint_information(self):
        ordered = list(enumerate(CLAIMS, start=1))
        attribution, numbering = judge.attribution_user_content("Q?", ordered, _texts)
        agreement, _ = judge.agreement_user_content("Q?", "Gold.", ordered)
        assert "Passage one." in attribution and "Gold." not in attribution
        assert "Gold." in agreement and "Passage" not in agreement and "EV_" not in agreement
        assert numbering == {1: 1, 2: 2}

    def test_stability_shuffles_claims_but_keeps_the_original_index(self):
        ordered = judge.shuffled_claims(CLAIMS * 3, seed=20260907)
        assert sorted(index for index, _ in ordered) == list(range(1, 7))
        _, numbering = judge.agreement_user_content("Q?", "Gold.", ordered)
        assert set(numbering.values()) == set(range(1, 7))

    def test_no_gold_variant_omits_the_gold_statement(self):
        content, _ = judge.agreement_user_content("Q?", None, list(enumerate(CLAIMS, start=1)))
        assert "Gold statement" not in content
        assert "guideline" in judge.NO_GOLD_PROMPT.lower()

    def test_miscitation_swaps_text_but_keeps_the_cited_id(self):
        swap = {"evidence_id": "EV_1", "replacement_evidence_id": "EV_3"}
        render = judge.swapped_passage_text(_texts, swap)
        content, _ = judge.attribution_user_content("Q?", [(1, CLAIMS[0])], render)
        assert "[evidence_id: EV_1]\n  Passage three." in content
        assert "Passage one." not in content


class TestJudgeParsing:
    def test_attribution_is_keyed_by_original_index_with_unknown_labels_unlabelable(self):
        payload = {
            "claims": [
                {
                    "index": 1,
                    "pair_attribution": [
                        {"evidence_id": "EV_3", "label": "ATTRIBUTABLE", "rationale": "r"},
                        {"evidence_id": "EV_9", "label": "ATTRIBUTABLE", "rationale": "not cited"},
                    ],
                    "joint_attribution": "SOMETHING_ELSE",
                    "rationale": "r",
                },
                {
                    "index": 2,
                    "pair_attribution": [],
                    "joint_attribution": "NO_SUPPORT",
                    "rationale": "r",
                },
            ]
        }
        numbering = {1: 2, 2: 1}
        cited = {2: ["EV_3"], 1: ["EV_1", "EV_2"]}
        parsed = judge.parse_attribution(payload, numbering, cited)
        assert parsed[2]["pair_attribution"] == {"EV_3": "ATTRIBUTABLE"}
        assert parsed[2]["joint_attribution"] == "UNLABELABLE"
        assert parsed[1]["pair_attribution"] == {"EV_1": "UNLABELABLE", "EV_2": "UNLABELABLE"}
        assert parsed[1]["joint_attribution"] == "NO_SUPPORT"

    def test_agreement_parsing_and_contract_violation(self):
        payload = {
            "claims": [
                {
                    "index": 1,
                    "agreement": "PARTIAL",
                    "eligibility_drop": True,
                    "eligibility_condition": "WEIGHT_OR_AGE_BAND",
                    "rationale": "r",
                }
            ]
        }
        parsed = judge.parse_agreement(payload, {1: 1})
        assert parsed[1]["agreement"] == "PARTIAL" and parsed[1]["eligibility_drop"] is True
        with pytest.raises(ValidationError):
            judge.parse_agreement({"claims": [{"index": 1}]}, {1: 1})

    def test_record_selection_is_seeded_and_answered_only(self):
        records = [
            {"question_id": f"Q{i}", "generation": {"abstained": False, "claims": [{}]}}
            for i in range(10)
        ] + [{"question_id": "Q99", "generation": {"abstained": True}}]
        first = judge.select_records(records, size=3, seed=1)
        second = judge.select_records(records, size=3, seed=1)
        assert [r["question_id"] for r in first] == [r["question_id"] for r in second]
        assert len(first) == 3 and all(r["question_id"] != "Q99" for r in first)


class TestOracle:
    RANKINGS = {
        "dense:question": ["a", "b", "c"],
        "sparse:question": ["d", "a"],
        "dense:gold": ["e"],
        "sparse:gold": ["f", "b"],
    }

    def test_known_item_recall_is_computed_before_the_served_ten_are_added(self):
        assert oracle.known_item_hit(["b"], self.RANKINGS) is True
        assert oracle.known_item_hit(["z"], self.RANKINGS) is False
        assert oracle.known_item_hit([], self.RANKINGS) is None

    def test_the_shortlist_unions_the_served_ten_and_cuts_at_the_size(self):
        shortlist = oracle.fuse_shortlist(
            self.RANKINGS, ["z", "a"], rrf_k=60, size=3, fuse=sweep.fuse_rrf
        )
        assert len(shortlist) == 3
        assert shortlist[0] == "a", "a is ranked in three lists"
        assert "z" in oracle.fuse_shortlist(
            self.RANKINGS, ["z"], rrf_k=60, size=10, fuse=sweep.fuse_rrf
        )

    def test_chunks_of_at_most_fifteen_and_the_union_of_present_verdicts(self):
        candidates = [f"EV_{i}" for i in range(25)]
        chunks = oracle.chunked(candidates, 15)
        assert [len(c) for c in chunks] == [15, 10]
        assert oracle.union_verdicts(
            [{"present": False}, {"present": True, "evidence_id": "EV_3"}]
        ) == (
            "PRESENT",
            "EV_3",
        )
        assert oracle.union_verdicts([{"present": False}, {"present": False}]) == ("ABSENT", None)
        assert oracle.union_verdicts([{"present": None}]) == (None, None)

    def test_the_widening_rule_follows_the_pre_stated_floors(self):
        assert oracle.widen_rule(0.95, 25)[0] == 25
        assert oracle.widen_rule(0.85, 25)[0] == 50
        size, note = oracle.widen_rule(0.75, 50)
        assert size == 50 and "lower bound" in note
        assert oracle.widen_rule(None, 25)[0] == 25

    def test_the_prompt_shows_gold_statement_and_numbered_candidates(self):
        content = oracle.oracle_user_content("Q?", "Gold.", ["EV_1", "EV_3"], _texts)
        assert "Recommendation statement:\nGold." in content
        assert "[1] evidence_id: EV_1\nPassage one." in content
        assert "[2] evidence_id: EV_3\nPassage three." in content
