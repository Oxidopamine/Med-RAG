# Separating promotion from validation in QA

Status: decided; the seam is implemented, tested and used. `release_assembly_service.py`
promotes through `promote_decided`, and `test_release_assembly.py` runs 10 of 10 with
nothing skipped (status corrected 2026-09-06; an earlier line said assembly did not yet
use the seam and that five composite tests were skipped)
Authored: 2026-08-30
Decided: 2026-08-30
Scope: the seam between QA deciding what is approved and a release being created from it

## Why this is forced

Serving holds one release ([narrative-corpus-composition.md](narrative-corpus-composition.md),
D1), so a multi-document corpus must arrive as one. `QAService.qa` currently does two jobs
in one call: it decides, and it promotes. Promotion binds evidence to a release id —
`CorpusEvidenceRecord.corpus_release_id` is inside the record, so the canonical digest
covers it, and `CanonicalEvidenceRow` refuses to rewrite an evidence ID under a different
digest.

The consequence is not a limitation, it is a contradiction: a per-document QA run promotes
its evidence under its own release id, and a composite can never re-point those records to
itself. Composite assembly hits this three separate ways, all symptoms of the same cause:

1. `CorpusReleaseBundle.verify_bundle_integrity` rejects a bundle whose evidence belongs to
   another release.
2. A manifest carries one attestation per stage, and each member's attestations are signed
   statements about *that member's* contents — they cannot be merged, and picking one
   member's would put a signature on the composite that does not cover what it contains.
3. The membership claim cannot be made atomic with a registration that has already happened
   in another call.

Patching any one of them individually produces something that looks composed and is not.

## Decision

**QA decides. A separate act promotes.** `QAService.qa` gains `promote: bool = True`, and a
new `DECIDED` run state sits between `PREPARED` and `VALIDATED`:

- `PREPARED` — evidence manifest sealed, anchors replayed.
- `DECIDED` — a complete, immutable approve/quarantine batch is sealed and attested. **New.**
  This is where a member of a composite stops.
- `VALIDATED` — evidence promoted to canonical records under a release id, release
  registered. Reached by single-document QA directly, or by assembly on behalf of members.

The canonical-record construction currently inside `QAService._promote` becomes a function
taking `(materialized records, decision batch, replays, release_id)` and returning canonical
records. QA calls it with its own release id; assembly calls it once with the composite's.
There is exactly one implementation, because two paths building canonical evidence
independently is how they stop agreeing about what a promoted record is — the same argument
that made the input closure and the materialization tail shared rather than duplicated.

## What does not change

- **The decision batch is unchanged and still sealed at the same point.** Nothing about what
  QA decides, or when it becomes immutable, moves. A `DECIDED` run has done every check a
  `VALIDATED` run has done; it has simply not been told which release it belongs to.
- **Single-document QA keeps its exact behaviour**, including `_release_id` derivation from
  `(candidate_sha256, batch_sha256)`. The HIV release's identity is committed to signed
  history and must not move. `promote=True` is the default precisely so no existing caller
  changes.
- **The registry gate is untouched.** Promotion still goes through
  `SQLCorpusReleaseRepository.register_candidate`, so a composite passes the same gate a
  single-document release does.

## Why `DECIDED` is a state and not a flag on the row

A run that has decided but not promoted is a real, durable condition — it is what every
member of a pending composite is in, possibly for as long as it takes the other twelve
documents to be acquired. The state machine should say so, and the CHECK constraint should
admit it, rather than the condition being inferred from `corpus_release_id IS NULL`. That
inference would also be ambiguous against a `PREPARED` run, which has no release either.

Migration 0019 widens `ck_corpus_qa_runs_state` and makes `corpus_release_id`,
`bundle_sha256` and `bundle_artifact_sha256` explicitly nullable for a `DECIDED` run, with a
constraint tying them together: a run is `VALIDATED` if and only if it carries all three.

## What this makes possible, and what it does not

Assembly becomes: take N `DECIDED` runs, check the properties that only exist across members
(no evidence-ID collision, one licensing revision, one release policy, agreeing inventory
snapshots), promote once under a single release id, sign that release's attestations over
its own content, register, record membership. The three symptoms above dissolve because none
of them was ever an assembly problem.

It does **not** make the composite atomic against a concurrent assembly on its own — that
needs the claim and the registration in one transaction, which this change makes reachable
but does not itself deliver. Recorded as the next step after it.

## Risk, stated plainly

This is the most safety-critical stage in the build plane and the one whose output the
active HIV release descends from. The mitigations are that `promote=True` is the default and
the single-document path is byte-for-byte the same sequence of calls, that the digest-history
guardrail pins the stored artifacts, and that the five currently-skipped composite tests
become the first executable proof the composite path works at all. If those tests do not go
green, the change is wrong and should not be argued into place.

## What landed

- **`QARunState.DECIDED`**, the CHECK constraint admitting it, and a second constraint
  tying state to columns in both directions: a run is `VALIDATED` if and only if it carries
  a release, a bundle digest and a bundle artifact. Migration 0019.
- **`QAService.qa(..., promote=True)`.** With `promote=False` the run stops after the
  decision batch is sealed, attested and stored, and is idempotent there. `QAResult` gained
  the matching validation: a decided result must have decided every record and must *not*
  carry a release.
- **`SQLQARepository.record_decision`**, which refuses to reduce an already-promoted run
  back to a decision.
- Three tests, including the regression that matters most — the single-document default
  still promotes and still produces one release per document, because the active HIV
  release descends from that path.

## Not done, and needed next

Assembly still selects `VALIDATED` members and merges their finished bundles. It has to
select `DECIDED` ones and promote them once instead, which means the canonical-record
construction, the release attestations and the bundle must become callable over N runs
rather than one `QACandidateContext`. Until that happens the five composite tests stay
skipped and nothing has actually been composed.

**Migration 0019 has not been validated against PostgreSQL.** 0016-0018 each were, by
running the chain on a throwaway database; the Docker daemon stopped partway through this
work, so 0019 has only been exercised through SQLAlchemy metadata in the test suite. The
constraint rewrite it performs is the same shape as 0016's and 0018's, which did apply
cleanly, but that is an argument rather than a check. Run it before relying on it.
