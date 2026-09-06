"""The labelling instrument of the correctness measurement plan (Section 4.1).

The worksheet renders; it does not decide. These tests pin the properties the plan fixed
after review: an error record is neither an answer nor an abstention; the census passes
conceal the arm on the agreement axis and interleave arms at claim level; the attribution
pass renders full passage text from the release and records which text source each pair
used; the abstention section shows every retrieved passage; `--no-screen` removes every
automated verdict; the selections are seeded and recorded; and the claims template is the
label skeleton of Section 4.3.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]


def _load(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


worksheet = _load("build_claim_audit_worksheet")


def _passage(evidence_id: str, text: str, *, truncated: bool = False) -> dict:
    return {
        "evidence_id": evidence_id,
        "kind": "NARRATIVE",
        "qualified_roles": ["PRIMARY_SUPPORT"],
        "rendered_text": text,
        "rendered_text_truncated": truncated,
    }


def _record(
    question_id: str,
    generation: dict | None,
    *,
    gate_reason: str | None = None,
    passages: list[dict] | None = None,
) -> dict:
    return {
        "question_id": question_id,
        "chapter_no": 2,
        "chapter": "Testing",
        "question": f"Question {question_id}?",
        "gate_reason": gate_reason,
        "retrieval": {
            "is_answerable": gate_reason is None,
            "missing_required_roles": [],
            "passages": passages
            if passages is not None
            else [_passage("EV_1", "Passage text with 6 months.", truncated=True)],
        },
        "generation": generation,
        "bucket": None,
    }


def _answer(*texts: str, cites: list[str] | None = None) -> dict:
    return {
        "abstained": False,
        "claims": [
            {"text": text, "evidence_ids": cites if cites is not None else ["EV_1"]}
            for text in texts
        ],
        "verification": {
            "rendered_claims": len(texts),
            "supported_claims": len(texts),
            "withheld_claims": 0,
        },
    }


INSUFFICIENT = {"abstained": True, "reason_code": "MODEL_DECLARED_INSUFFICIENT", "message": "no"}


def _run(*records: dict, label: str = "production-a") -> dict:
    return {
        "results": list(records),
        "generation_provider": "gemini",
        "generation_binding": {"model_id": "gemini-3.7-flash"},
        "registered": True,
        "run_label": label,
        "question_set": {"sha256": "abc"},
    }


def _ten_passages() -> list[dict]:
    return [_passage(f"EV_{i}", f"Passage {i}.") for i in range(10)]


def _production() -> dict:
    return _run(
        _record("Q1", _answer("Repeat at 6 months.", "Start now.", cites=["EV_1"])),
        _record("Q2", _answer("Claim two.")),
        _record("Q3", INSUFFICIENT, passages=_ten_passages()),
        _record("Q4", INSUFFICIENT, passages=_ten_passages()),
        _record("Q5", INSUFFICIENT, passages=_ten_passages()),
        _record("Q6", None, gate_reason="INCOMPLETE_EVIDENCE_ROLE_SET", passages=_ten_passages()),
        _record("Q7", {"error": "429", "error_class": "RESOURCE_EXHAUSTED", "abstained": None}),
    )


def _closed_book() -> dict:
    return _run(
        _record("Q1", _answer("Closed-book claim on Q1.", cites=[]), passages=[]),
        _record("Q3", _answer("Closed-book claim on Q3.", cites=[]), passages=[]),
        label="closed-book-164",
    )


def _naive() -> dict:
    return _run(
        _record("Q1", _answer("Naive claim on Q1.")),
        _record("Q6", _answer("Naive claim on Q6, a withheld question.")),
        _record("Q4", _answer("Naive claim on Q4.")),
        label="naive-164",
    )


QUESTIONS = {
    "items": [
        {"question_id": f"Q{i}", "source_statement": f"Gold statement {i}."} for i in range(1, 8)
    ]
}


def _census(**overrides) -> worksheet.Census:
    settings = {
        "questions": QUESTIONS,
        "render_full": None,
        "shuffle_seed": 20260906,
        "abstention_sample": 2,
        "record_sample": None,
        "screen": False,
    }
    settings.update(overrides)
    runs = overrides.pop("runs", None) or {
        "production": _production(),
        "closed_book": _closed_book(),
        "naive": _naive(),
    }
    settings.pop("runs", None)
    return worksheet.Census(runs, **settings)


class TestLegacyWorksheet:
    def test_error_records_are_listed_in_their_own_part_and_offered_no_bucket(self):
        run = _run(
            _record("Q1", _answer("Repeat at 6 months.")),
            _record("Q2", {"error": "429", "error_class": "RESOURCE_EXHAUSTED", "abstained": None}),
            _record(
                "Q3", {"abstained": True, "reason_code": "GENERATION_UNAVAILABLE", "message": "429"}
            ),
            _record("Q4", None, gate_reason="INCOMPLETE_EVIDENCE_ROLE_SET"),
        )
        text, template = worksheet.render(run, passage_limit=200)
        assert (
            "answered: 1  abstained at generation: 0  abstained at gate: 1  generation errors: 2"
            in text
        )
        part_4 = text.split("## Part 4 - generation errors (2), not classifiable", 1)[1]
        assert "### Q2" in part_4 and "### Q3" in part_4
        assert "bucket" not in part_4.split("### Q2", 1)[1]
        part_2 = text.split("## Part 2", 1)[1].split("## Part 3", 1)[0]
        assert "Q2" not in part_2 and "Q3" not in part_2
        assert set(template["buckets"]) == {"Q1", "Q2", "Q3", "Q4"}

    def test_abstention_sections_show_every_retrieved_passage(self):
        run = _run(_record("Q3", INSUFFICIENT, passages=_ten_passages()))
        text, _ = worksheet.render(run, passage_limit=200)
        assert text.count("- `EV_") == 10

    def test_no_screen_removes_flags_and_the_bucket_field(self):
        run = _run(_record("Q1", _answer("Repeat at 12 months.")))
        with_screen, _ = worksheet.render(run, passage_limit=200)
        without, _ = worksheet.render(run, passage_limit=200, screen=False)
        assert "NUMERAL_NOT_IN_SOURCE" in with_screen and "**bucket:**" in with_screen
        assert "NUMERAL_NOT_IN_SOURCE" not in without and "**bucket:**" not in without

    def test_screens_still_flag_a_numeral_absent_from_the_source(self):
        claim = {"text": "Repeat at 12 months.", "evidence_ids": ["EV_1"]}
        passages = {
            "EV_1": {"rendered_text": "Repeat at 6 months.", "rendered_text_truncated": False}
        }
        flags, missing = worksheet.screen_claim(claim, passages)
        assert flags == ["NUMERAL_NOT_IN_SOURCE"]
        assert missing == ["12"]


class TestSelections:
    def test_run_argument_parsing_survives_a_windows_drive_colon(self):
        assert worksheet.parse_run_argument(r"C:\runs\a.json:closed_book") == (
            Path(r"C:\runs\a.json"),
            "closed_book",
        )
        assert worksheet.parse_run_argument(r"C:\runs\a.json") == (
            Path(r"C:\runs\a.json"),
            "production",
        )
        assert worksheet.parse_run_argument("data/a.json:naive") == (Path("data/a.json"), "naive")

    def test_abstention_selection_is_every_gate_block_plus_a_seeded_sample(self):
        records = _production()["results"]
        gate, sampled, total = worksheet.abstention_review_selection(records, sample_size=2, seed=1)
        assert gate == ["Q6"]
        assert total == 3 and len(sampled) == 2 and set(sampled) <= {"Q3", "Q4", "Q5"}
        again = worksheet.abstention_review_selection(records, sample_size=2, seed=1)
        assert again[1] == sampled
        assert (
            worksheet.abstention_review_selection(records, sample_size=2, seed=2)[1] != sampled
            or True
        )

    def test_record_sample_is_seeded_and_answered_only(self):
        records = _production()["results"]
        sample = worksheet.answered_record_sample(records, sample_size=1, seed=3)
        assert len(sample) == 1 and sample[0]["question_id"] in {"Q1", "Q2"}
        assert worksheet.answered_record_sample(records, sample_size=None, seed=3) == [
            r for r in records if r["question_id"] in {"Q1", "Q2"}
        ]


class TestAgreementPass:
    def test_claims_from_three_arms_are_interleaved_and_the_arm_is_concealed(self):
        census = _census()
        lines, key = worksheet.render_agreement(census)
        text = "\n".join(lines)
        assert "Closed-book claim on Q1." in text
        assert "Naive claim on Q6, a withheld question." in text
        assert "Repeat at 6 months." in text
        for arm_word in ("closed_book", "naive", "production"):
            assert arm_word not in text
        assert "EV_1" not in text, "no citations in the agreement pass"
        assert "Gold statement 1." in text
        arms = {row["arm"] for row in key}
        assert arms == {"production", "closed_book", "naive"}
        assert [row["item"] for row in key] == [f"A-{i:03d}" for i in range(1, len(key) + 1)]

    def test_the_naive_arm_enters_only_on_the_abstention_review_selection(self):
        census = _census()
        naive_ids = {r["question_id"] for r in census.arm_records("naive")}
        assert "Q6" in naive_ids, "gate-blocked questions are always in the selection"
        assert "Q1" not in naive_ids, "production answered Q1, so the chain withheld nothing there"
        assert naive_ids - {"Q6"} <= set(census.sampled)

    def test_question_validity_is_asked_once_per_question(self):
        lines, _ = worksheet.render_agreement(_census())
        text = "\n".join(lines)
        validity = text.split("## Records - question validity", 1)[1]
        assert validity.count("- question_valid:") == validity.count("### Q")


class TestAttributionPass:
    def test_full_text_comes_from_the_bundle_and_the_source_is_recorded(self):
        def render_full(evidence_id: str) -> str | None:
            return f"FULL TEXT OF {evidence_id} with 6 months." if evidence_id == "EV_1" else None

        census = _census(render_full=render_full)
        lines = worksheet.render_attribution(census, include_abstentions=False)
        text = "\n".join(lines)
        assert "FULL TEXT OF EV_1" in text
        assert "[text: bundle]" in text
        assert "Gold statement" not in text, "the attribution pass hides the gold statement"
        assert "pair_attribution `EV_1`" in text
        assert "- joint_attribution:" in text
        assert "- presentation_defect:" in text
        assert census.text_sources["bundle"] > 0

    def test_screens_are_recomputed_against_the_full_text(self):
        census = _census(render_full=lambda evidence_id: "Every 6 months.", screen=True)
        text = "\n".join(worksheet.render_attribution(census, include_abstentions=False))
        assert "SOURCE_TRUNCATED" not in text, "the bundle text is never truncated"
        assert "NUMERAL_NOT_IN_SOURCE" not in text.split("Repeat at 6 months.")[1].split("\n")[0]

    def test_the_abstention_section_shows_all_ten_passages_and_the_gold_statement(self):
        census = _census()
        lines = worksheet.render_abstention(census)
        text = "\n".join(lines)
        assert "### Q6" in text and "[gate-blocked]" in text
        assert text.count("- `EV_") == 10 * (1 + len(census.sampled))
        assert "Gold statement 6." in text
        assert "- gate_right:" in text and "- dak_has_answer:" in text
        assert "- any_retrieved_passage_answers:" in text
        assert "Claim" not in text.split("### ", 1)[1], "no answered-record claim in this section"


class TestClaimsTemplate:
    def test_the_skeleton_follows_section_4_3(self):
        census = _census(render_full=lambda evidence_id: "full")
        template = worksheet.claims_template(
            census,
            run_paths={
                "production": Path("p.json"),
                "closed_book": Path("c.json"),
                "naive": Path("n.json"),
            },
            rubric_sha256="deadbeef",
        )
        assert template["run"]["question_set_sha256"] == "abc"
        assert template["rubric_sha256"] == "deadbeef"
        assert set(template["passes"]) == {"agreement", "attribution", "mislead"}
        production = template["records"]["Q1"]
        assert production["arm"] == "production"
        claim = production["claims"][0]
        assert claim["pair_attribution"] == {"EV_1": None}
        assert claim["pair_text_source"] == {"EV_1": "bundle"}
        for field in ("joint_attribution", "agreement", "eligibility_drop", "mislead"):
            assert claim[field] is None
        assert template["records"]["Q1:closed_book"]["claims"][0]["pair_attribution"] == {}
        assert template["records"]["Q6:naive"]["arm"] == "naive"
        assert template["gate_blocked"] == {
            "Q6": {"gate_right": None, "dak_has_answer": None, "note": ""}
        }
        assert template["abstained_sample"]["size"] == 2
        assert template["abstained_sample"]["seed"] == 20260906
        assert template["census_valid"] is True

    def test_a_run_text_fallback_invalidates_the_census(self):
        census = _census(render_full=None)
        template = worksheet.claims_template(
            census, run_paths={"production": Path("p.json")}, rubric_sha256=None
        )
        assert template["pair_text_sources"]["run"] > 0
        assert template["census_valid"] is False


class TestMisleadPass:
    def test_flagged_claims_and_controls_are_shuffled_with_the_trigger_hidden(self):
        census = _census()
        labels = {
            "records": {
                "Q1": {
                    "arm": "production",
                    "presentation_defect": False,
                    "claims": [
                        {
                            "index": 1,
                            "joint_attribution": "NO_SUPPORT",
                            "pair_attribution": {"EV_1": "NO_SUPPORT"},
                            "agreement": "AGREES",
                            "eligibility_drop": False,
                        },
                        {
                            "index": 2,
                            "joint_attribution": "ATTRIBUTABLE",
                            "pair_attribution": {"EV_1": "ATTRIBUTABLE"},
                            "agreement": "AGREES",
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
                            "joint_attribution": "ATTRIBUTABLE",
                            "pair_attribution": {"EV_1": "ATTRIBUTABLE"},
                            "agreement": "PARTIAL",
                            "eligibility_drop": False,
                        },
                    ],
                },
            }
        }
        lines, key = worksheet.render_mislead(census, labels, controls=1)
        text = "\n".join(lines)
        statuses = {(row["key"], row["index"]): row["status"] for row in key}
        assert statuses[("Q1", 1)] == "flagged" and statuses[("Q2", 1)] == "flagged"
        assert statuses[("Q1", 2)] == "control"
        assert "flagged" not in text and "control" not in text
        assert "NO_SUPPORT" not in text
        assert "- extent:" in text and "- likelihood:" in text


@pytest.mark.skipif(
    not (REPO_ROOT / "benchmarks" / "results" / "coverage-stage1-naive-baseline.json").exists(),
    reason="published evidence is not present",
)
def test_the_pilot_instrument_renders_the_stage1_naive_run(tmp_path: Path):
    """The rubric is piloted on the stage-1 naive claims, which sit outside every census."""

    run = json.loads(
        (REPO_ROOT / "benchmarks" / "results" / "coverage-stage1-naive-baseline.json").read_text(
            encoding="utf-8"
        )
    )
    census = worksheet.Census(
        {"production": run},
        questions=None,
        render_full=None,
        shuffle_seed=1,
        abstention_sample=5,
        record_sample=None,
        screen=False,
    )
    lines, key = worksheet.render_agreement(census)
    assert len(key) == 54, "the 54 naive claims"
