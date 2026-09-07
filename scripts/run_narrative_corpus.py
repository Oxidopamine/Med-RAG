"""Drive the narrative pipeline over every item of one reconciliation candidate.

Closure -> census -> materialization -> QA (decide, do not promote) for each item, then
one composite release over everything that cleared. In-process rather than one CLI
invocation per stage per item: the CLI reloads the model stack on every start, which at a
few hundred documents dominates the run.

Per-item failures are recorded and skipped rather than aborting the batch. A document that
cannot be extracted, or that a licence forbids, must not stop the other 284 - but it must
also never be silently counted as present, so the summary lists every skip with its reason
and the composite is assembled only over runs that actually reached DECIDED.

Usage:

    python scripts/run_narrative_corpus.py <reconciliation_candidate_id> [--assemble]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.core.config import get_settings
from app.corpus.releases import SQLCorpusReleaseRepository
from app.corpus_steward.connectors import HTTPConnectorTransport
from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.evidence_extractor import DAKSourceExtractor
from app.corpus_steward.fhir_package import FHIRPackageParser
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.materialization_repository import (
    SQLMaterializationRepository,
)
from app.corpus_steward.materialization_service import (
    MaterializationService,
)
from app.corpus_steward.narrative_extractor import (
    NarrativeSourceExtractor,
)
from app.corpus_steward.narrative_repository import (
    SQLNarrativeAnalysisRepository,
)
from app.corpus_steward.narrative_service import (
    NarrativeSourceAnalysisService,
)
from app.corpus_steward.qa_repository import SQLQARepository
from app.corpus_steward.qa_schemas import QARunState
from app.corpus_steward.qa_service import QAService
from app.corpus_steward.registry import (
    SQLAttestationRepository,
    SQLTrustRootRegistry,
)
from app.corpus_steward.release_assembly_service import (
    ReleaseAssemblyError,
    ReleaseAssemblyService,
)
from app.corpus_steward.schemas import ReconciliationReleaseCandidate
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import (
    SQLStructuredInputRepository,
)
from app.corpus_steward.structured_input_service import (
    StructuredInputClosureService,
)
from app.corpus_steward.structured_repository import (
    SQLStructuredPackageRepository,
)
from app.persistence.database import Database
from app.persistence.models import ReconciliationReleaseCandidateRow


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_id")
    parser.add_argument(
        "--signing-key",
        default="data/local/phase1-validation-20260825/stage-private.pem",
    )
    parser.add_argument("--signing-key-id", default="local-steward-stage")
    parser.add_argument("--signer-identity", default="local-corpus-steward")
    parser.add_argument(
        "--assemble", action="store_true", help="compose one release at the end"
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--report", type=Path, default=Path(".logs/narrative-corpus-run.json")
    )
    args = parser.parse_args()

    settings = get_settings()
    database = Database(settings.database_url)
    signer = Ed25519Signer.from_pem(
        Path(args.signing_key),
        key_id=args.signing_key_id,
        signer_identity=args.signer_identity,
    )
    artifacts = ImmutableStewardArtifactStore(settings.steward_artifact_store_path)
    ledger = SQLReconciliationLedger(database)
    attestations = SQLAttestationRepository(database)
    trust_roots = SQLTrustRootRegistry(database)
    narrative_repository = SQLNarrativeAnalysisRepository(database)
    transport = HTTPConnectorTransport(
        max_bytes=settings.steward_max_artifact_bytes,
        timeout_seconds=settings.steward_request_timeout_seconds,
        allow_private_networks=settings.steward_allow_private_networks,
    )

    closures = StructuredInputClosureService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        parser=FHIRPackageParser(),
        transport=transport,
        signer=signer,
    )
    census = NarrativeSourceAnalysisService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        repository=narrative_repository,
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        signer=signer,
    )
    materializer = MaterializationService(
        trust_roots=trust_roots,
        source_repository=SQLStructuredPackageRepository(database),
        input_repository=SQLStructuredInputRepository(database),
        repository=SQLMaterializationRepository(database),
        ledger=ledger,
        attestations=attestations,
        artifacts=artifacts,
        extractor=DAKSourceExtractor(),
        signer=signer,
        narrative_repository=narrative_repository,
        narrative_extractor=NarrativeSourceExtractor(),
    )
    qa = QAService(
        repository=SQLQARepository(database),
        releases=SQLCorpusReleaseRepository(database),
        trust_roots=trust_roots,
        attestations=attestations,
        ledger=ledger,
        artifacts=artifacts,
        extractor=DAKSourceExtractor(),
        signer=signer,
    )

    async with database.session() as session:
        row = await session.get(ReconciliationReleaseCandidateRow, args.candidate_id)
        if row is None:
            print(f"reconciliation candidate not found: {args.candidate_id}")
            return 2
        candidate = ReconciliationReleaseCandidate.model_validate(row.payload)
    items = [artifact.item_id for artifact in candidate.content.source_artifacts]
    if args.limit:
        items = items[: args.limit]
    print(f"candidate {args.candidate_id}: {len(items)} item(s)\n", flush=True)

    decided: list[str] = []
    skipped: list[dict[str, str]] = []
    started = time.time()
    for index, item_id in enumerate(items, start=1):
        label = f"[{index}/{len(items)}] {item_id[:8]}"
        try:
            closure = await closures.resolve(args.candidate_id, item_id=item_id)
            if closure.state.value != "RESOLVED":
                raise RuntimeError(
                    f"closure {closure.state.value}: {list(closure.report.content.blockers)[:2]}"
                )
            analysis = await census.analyse(args.candidate_id, item_id=item_id)
            if not analysis.report.content.promotion_eligible:
                raise RuntimeError(
                    f"census blocked: {list(analysis.report.content.blockers)[:2]}"
                )
            result = await materializer.materialize_narrative(
                args.candidate_id, item_id=item_id
            )
            corpus_candidate = result.corpus_release_candidate
            if corpus_candidate is None:
                raise RuntimeError(
                    f"materialization blocked: {list(result.report.content.blockers)[:2]}"
                )
            qa_result = await qa.qa(
                corpus_candidate.content.corpus_release_candidate_id, promote=False
            )
            # `qa()` returns early for a run that is already VALIDATED, so a document
            # promoted by an earlier single-document run comes back promoted rather than
            # decided. Filing that as a composite member would have assembly reject the
            # whole batch at the end, after every other document had been processed.
            if qa_result.state is not QARunState.DECIDED:
                raise RuntimeError(
                    f"QA run {qa_result.qa_run_id} is {qa_result.state.value}, not DECIDED; "
                    "it belongs to an existing release and cannot join a composite"
                )
            decided.append(qa_result.qa_run_id)
            print(
                f"{label} OK  units={analysis.report.content.unit_count_total:4d} "
                f"approved={qa_result.approved_count:4d} quarantined={qa_result.quarantined_count:3d}",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001 - one document must not stop the batch
            reason = f"{type(error).__name__}: {error}"
            skipped.append({"item_id": item_id, "reason": reason[:300]})
            print(f"{label} SKIP {reason[:150]}", flush=True)

    elapsed = time.time() - started
    print(
        f"\ndecided {len(decided)} / skipped {len(skipped)} in {elapsed / 60:.1f} min",
        flush=True,
    )

    assembly_id = None
    if args.assemble and decided:
        assembler = ReleaseAssemblyService(
            database=database,
            qa=qa,
            releases=SQLCorpusReleaseRepository(database),
            ledger=ledger,
            attestations=attestations,
            artifacts=artifacts,
            signer=signer,
        )
        try:
            assembly = await assembler.assemble(tuple(decided))
            assembly_id = assembly.content.corpus_release_id
            print(
                f"composite release {assembly_id} over {len(assembly.content.members)} members, "
                f"{assembly.content.evidence_count} evidence records",
                flush=True,
            )
        except ReleaseAssemblyError as error:
            print(f"assembly refused: {error}", flush=True)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            {
                "candidate_id": args.candidate_id,
                "decided_qa_runs": decided,
                "skipped": skipped,
                "composite_release_id": assembly_id,
                "elapsed_seconds": round(elapsed, 1),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"report: {args.report}")
    await database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
