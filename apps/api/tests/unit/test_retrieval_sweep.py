"""The pure parts of the retrieval sweep: fusion, recall at K and role coverage."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.schemas.domain import SERVABLE_LIFECYCLE_STATES  # noqa: E402


def _load(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sweep = _load("retrieval_sweep")


def test_rrf_fusion_rewards_agreement_and_breaks_ties_deterministically():
    fused = sweep.fuse_rrf({"dense": ["a", "b", "c"], "sparse": ["b", "d", "a"]}, rrf_k=60)
    assert fused[:2] == ["a", "b"] or fused[:2] == ["b", "a"]
    # a: 1/61 + 1/63; b: 1/62 + 1/61 -> b first.
    assert fused[0] == "b"
    assert fused[-1] in {"c", "d"}


def test_recall_at_k_counts_distinct_targets():
    assert sweep.recall_at_k(["a", "b", "c"], ["a", "a", "c"], 2) == 0.5
    assert sweep.recall_at_k(["a", "b", "c"], [], 2) is None


def test_required_roles_are_qualified_by_form():
    qualified = {"a": frozenset({"PRIMARY_SUPPORT"}), "b": frozenset({"APPLICABILITY"})}
    assert sweep.roles_covered(["a", "b"], qualified, 2) is True
    assert sweep.roles_covered(["a", "b"], qualified, 1) is False
    assert sweep.roles_covered(["a", "x"], qualified, 2) is False


def test_sweep_metrics_report_every_lane_and_k():
    rankings = {
        "Q1": {"sparse": ["a", "b"], "dense": ["b", "a"], "hybrid": ["a", "b"]},
        "Q2": {"sparse": ["c", "d"], "dense": ["d", "c"], "hybrid": ["c", "d"]},
    }
    metrics = sweep.sweep_metrics(
        rankings,
        cited={"Q1": ["a"]},
        present={"Q2": "d"},
        qualified_roles={
            "a": frozenset({"PRIMARY_SUPPORT", "APPLICABILITY"}),
            "d": frozenset({"PRIMARY_SUPPORT"}),
        },
        ks=(1, 2),
    )
    assert metrics["sparse"]["1"]["cited_id_recall_mean"] == 1.0
    assert metrics["dense"]["1"]["cited_id_recall_mean"] == 0.0
    assert metrics["dense"]["2"]["cited_id_recall_mean"] == 1.0
    assert metrics["sparse"]["1"]["oracle_present_recall"] == (0, 1)
    assert metrics["dense"]["1"]["oracle_present_recall"] == (1, 1)
    assert metrics["sparse"]["1"]["required_roles_covered"] == (1, 2)


def test_the_release_filter_is_the_serving_filter():
    query_filter = sweep.release_filter("CR_1")
    keys = [clause["key"] for clause in query_filter["must"]]
    assert keys == ["corpus_release_id", "approval_status", "lifecycle_status"]
    assert query_filter["must"][2]["match"]["any"] == [
        status.value for status in SERVABLE_LIFECYCLE_STATES
    ]
