"""The pure parts of the coupling metric and the entailment audit (plan Sections 5.2, 8.2)."""

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


coupling = _load("lexical_coupling")
audit = _load("entailment_audit")


class TestCoupling:
    def test_the_token_rule_is_source_term_overlaps(self):
        assert coupling.content_tokens("When should ART be started in adults?") == {
            "when",
            "should",
            "started",
            "adults",
        }

    def test_coupling_is_the_share_of_tokens_found_as_substrings(self):
        value = coupling.coupling(
            "When should treatment be started?", ["Start treatment when the count falls."]
        )
        # when, should, treatment, started -> when, treatment found ("started" is not a
        # substring of "start"); should is absent.
        assert value == 0.5
        assert coupling.coupling("ART now", ["anything"]) is None

    def test_terciles_split_into_thirds_and_are_recorded_as_numbers(self):
        values = [i / 10 for i in range(10)]
        boundaries = coupling.tercile_boundaries(values)
        assert len(boundaries) == 2 and boundaries[0] < boundaries[1]
        assert coupling.tercile_of(0.0, boundaries) == 1
        assert coupling.tercile_of(0.9, boundaries) == 3

    def test_compute_writes_one_value_per_question_and_no_text(self):
        run = {
            "results": [
                {
                    "question_id": "Q1",
                    "question": "When should treatment start?",
                    "retrieval": {"passages": [{"evidence_id": "EV_1"}]},
                    "generation": {"abstained": False, "claims": [{"text": "x"}]},
                    "source_term_overlap": 0.4,
                },
                {
                    "question_id": "Q2",
                    "question": "Which infants should be tested?",
                    "retrieval": {"passages": [{"evidence_id": "EV_2"}]},
                    "generation": None,
                },
            ]
        }
        texts = {"EV_1": "Treatment should start now.", "EV_2": "Unrelated."}
        result = coupling.compute(run, lambda evidence_id: texts.get(evidence_id))
        assert set(result["questions"]) == {"Q1", "Q2"}
        # when, should, treatment, start: three of four occur in the passage.
        assert result["questions"]["Q1"]["coupling"] == 0.75
        assert result["questions"]["Q2"]["coupling"] == 0.0
        assert result["questions"]["Q1"]["source_term_overlap"] == 0.4
        assert "Treatment" not in str(result)
        assert result["summary"]["mean_by_outcome"] == {"ABSTAINED": 0.0, "ANSWERED": 0.75}


class _FakeScorer:
    name = "fake"

    def score(self, pairs):
        return [0.9 if "6 months" in premise else 0.1 for premise, _ in pairs]


class TestEntailmentAudit:
    def _run(self) -> dict:
        return {
            "results": [
                {
                    "question_id": "Q1",
                    "retrieval": {
                        "passages": [
                            {"evidence_id": "EV_1", "kind": "NARRATIVE"},
                            {"evidence_id": "EV_2", "kind": "DECISION_RULE"},
                        ]
                    },
                    "generation": {
                        "abstained": False,
                        "claims": [
                            {
                                "text": "Repeat viral load at 6 months.",
                                "evidence_ids": ["EV_1", "EV_2"],
                            },
                            {"text": "Start ART today.", "evidence_ids": ["EV_2"]},
                        ],
                    },
                },
                {"question_id": "Q2", "generation": {"abstained": True, "reason_code": "X"}},
            ]
        }

    def test_pairs_and_claims_are_collected_with_premise_kinds(self):
        texts = {"EV_1": "Viral load is repeated at 6 months.", "EV_2": "IF condition THEN act."}
        pairs, claims, missing = audit.collect_items(self._run(), lambda e: texts.get(e))
        assert missing == 0
        assert [(p["claim_index"], p["evidence_id"], p["premise_kind"]) for p in pairs] == [
            (1, "EV_1", "NARRATIVE"),
            (1, "EV_2", "DECISION_RULE"),
            (2, "EV_2", "DECISION_RULE"),
        ]
        assert [c["premise_mix"] for c in claims] == ["mixed", "tabular"]
        assert claims[0]["premise"] == texts["EV_1"] + "\n\n" + texts["EV_2"]

    def test_scores_and_the_lexical_floor_are_attached_and_text_is_dropped(self):
        texts = {"EV_1": "Viral load is repeated at 6 months.", "EV_2": "IF condition THEN act."}
        pairs, claims, _ = audit.collect_items(self._run(), lambda e: texts.get(e))
        scored_pairs, _ = audit.score_items(_FakeScorer(), pairs)
        scored_claims, _ = audit.score_items(_FakeScorer(), claims)
        assert [p["score"] for p in scored_pairs] == [0.9, 0.1, 0.1]
        assert scored_claims[0]["score"] == 0.9
        assert all("premise" not in p and "hypothesis" not in p for p in scored_pairs)
        # "repeat viral load months": viral, load, months found in EV_1; repeat is a
        # substring of "repeated".
        assert scored_pairs[0]["lexical_score"] == 1.0
        assert scored_pairs[1]["lexical_score"] == 0.0

    def test_lexical_mode_loads_no_model(self):
        scorer, attempts = audit.build_instrument("lexical")
        assert scorer is None and attempts == []
        scored, _ = audit.score_items(
            None, [{"question_id": "Q", "claim_index": 1, "premise": "a b", "hypothesis": "c"}]
        )
        assert scored[0]["score"] is None

    def test_premise_mix_and_prose_rule(self):
        assert audit.premise_mix(["NARRATIVE", "NARRATIVE_SUPPORTING"]) == "prose"
        assert audit.premise_mix(["SCHEDULE_ENTRY"]) == "tabular"
        assert audit.premise_mix(["NARRATIVE", "DATA_DICTIONARY_ENTRY"]) == "mixed"

    def test_the_pinned_revisions_are_the_plans(self):
        assert audit.INSTRUMENTS["hhem"]["revision"] == "8e4a2e6e96c708cc76c2344f7e4757df2515292c"
        assert (
            audit.INSTRUMENTS["deberta"]["revision"] == "6f5cf0a2b59cabb106aca4c287eed12e357e90eb"
        )
        assert (
            audit.INSTRUMENTS["minicheck"]["revision"] == "96eafd01cee2d16cf81aaa2fb226b14f422a37b3"
        )
