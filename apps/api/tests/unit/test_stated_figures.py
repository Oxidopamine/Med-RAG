"""The figures stated in prose must match the runs they came from.

`README.md` and `docs/` restate numbers produced by runs under `benchmarks/results/`. Duplication
across those documents is deliberate -- the README is meant to be readable on its own -- so the
protection against divergence has to be executable rather than editorial. These tests fail if a
stated figure stops matching its artifact, or if two documents state the same quantity differently.

They are unit tests with no database, network, or model: they read committed JSON and markdown.
"""

from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = REPO_ROOT / "scripts"


def _run(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *arguments],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


@pytest.mark.skipif(
    not (REPO_ROOT / "benchmarks" / "results").exists(),
    reason="published evidence is not present in this checkout",
)
def test_stated_figures_match_the_published_runs() -> None:
    result = _run("check_readme_figures.py")
    assert result.returncode == 0, (
        "A figure stated in the README or docs no longer matches the run that produced it, "
        "or two documents disagree:\n" + result.stdout + result.stderr
    )


@pytest.mark.skipif(
    not (REPO_ROOT / "data" / "local").exists(),
    reason="local run store is not present; published evidence cannot be re-derived here",
)
def test_published_evidence_is_current() -> None:
    result = _run("publish_evidence.py", "--check")
    assert result.returncode == 0, (
        "benchmarks/results/ has drifted from the local run store. Re-run "
        "`python scripts/publish_evidence.py`:\n" + result.stdout + result.stderr
    )


@pytest.mark.skipif(
    not (REPO_ROOT / "benchmarks" / "results").exists(),
    reason="published evidence is not present in this checkout",
)
def test_published_coverage_runs_carry_no_corpus_text() -> None:
    """Redaction is a licensing obligation, so it is asserted rather than trusted."""
    import json

    published = sorted((REPO_ROOT / "benchmarks" / "results").glob("coverage-*.json"))
    assert published, "no published coverage runs found"

    scanned = 0
    for path in published:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["_redaction"]["removed_field_count"] > 0
        for result in document["results"]:
            for passage in (result.get("retrieval") or {}).get("passages", []):
                scanned += 1
                assert "rendered_text" not in passage, f"{path.name} leaks corpus text"
                assert "rendered_text_truncated" not in passage, f"{path.name} leaks corpus text"
    assert scanned > 0


@pytest.mark.skipif(
    not (REPO_ROOT / "benchmarks" / "results").exists(),
    reason="published evidence is not present in this checkout",
)
def test_published_sealed_reports_self_verify() -> None:
    """A sealed report is only evidence if its digest still covers its content."""
    import json

    from app.schemas.corpus import canonical_sha256

    reports = sorted(
        path
        for path in (REPO_ROOT / "benchmarks" / "results").glob("*.json")
        if not path.name.startswith("coverage-")
    )
    assert reports, "no published sealed reports found"

    for path in reports:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert canonical_sha256(document["content"]) == document["report_sha256"], (
            f"{path.name}: report_sha256 does not cover its own content"
        )


def test_publisher_and_checker_are_importable() -> None:
    """Guard against a rename that silently disables the checks above."""
    for script in ("publish_evidence.py", "check_readme_figures.py"):
        assert (SCRIPTS / script).exists(), f"scripts/{script} is missing"
    runpy.run_path(str(SCRIPTS / "check_readme_figures.py"), run_name="__not_main__")
