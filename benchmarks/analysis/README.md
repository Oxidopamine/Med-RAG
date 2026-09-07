# Analysis artifacts of the correctness measurement plan

The non-run artifacts of
[docs/correctness-measurement-plan.md](../../docs/correctness-measurement-plan.md)
(Section 9.3). Run files stay in [`../results/`](../results/), published redacted by
`scripts/publish_evidence.py`; everything here is derived from them, from human labels, or
from judge and oracle calls, and **none of it carries WHO passage text**. Judge and oracle
outputs carry prompt templates and the sha256 of each filled prompt, never the filled
prompt, because a filled attribution prompt holds passage text the release marks
`render_allowed: false`.

This directory is separate from `results/` because the tests over that directory assume
every non-`coverage-` JSON is a sealed report carrying `report_sha256` over its `content`,
and every `coverage-` JSON is a redacted run. The files below are neither.

| File | Written by | What it is |
|---|---|---|
| `retrieval-overlap-stage2.json` | `scripts/retrieval_overlap.py` | The four retrieval-concentration figures of plan Section 5.1, recomputed from the published stage-2 run |
| `retrieval-overlap-production-a.json` | `scripts/retrieval_overlap.py` | The same four figures over production A's retrieved sets |
| `mde.json` | `scripts/cm_statistics.py --write-mde` | The minimum detectable effect tables of Section 3.5 and the prior-grid half-widths of Section 5.2, timestamped before the naive file is opened |
| `coupling.json` | `scripts/lexical_coupling.py` | Question-to-retrieved-passage lexical coupling per question with tercile boundaries, computed before any label is viewed |
| `environment.txt` | `pip freeze` | The Python environment the statistics ran in |
| `labels-<annotator>-<yyyymmdd>.json` | the annotator, through the worksheets | The label file of Section 4.3; Appendix B is its data dictionary |
| `judge-*.json`, `oracle-*.json`, `checker-*.json` | `judge_claims.py`, `abstention_oracle.py`, `entailment_audit.py` | Second signals, validated against the human census before they are reported |
| `retrieval-sweep-production-a.json` | `scripts/retrieval_sweep.py` | Recall at K per lane of the IDs production A cited, and required-role coverage at each K (Section 3.6); IDs and ranks only |
| `cm-statistics-horizon1.json` | `scripts/cm_statistics.py --sections placeholders,3.5` | The Horizon 1 quantities: recomputed placeholders, the noise floor, the negative control, Q5 and the MDE tables |
| `cm-statistics.json` | `scripts/cm_statistics.py` | Every number in Sections 1.2, 3.5, 4.5, 4.6, 5.2, 6.2 and 7.3 as one JSON, once labels exist |

Every file records `rubric_sha256`, the sha256 of the plan as deposited at OSF; a file whose
hash differs was produced under a revised rubric and is reported as such. This README is in
the scope of `scripts/check_readme_figures.py`, so a figure restated here that disagrees with
another document fails CI.
