"""Compose many QA'd documents into the one release serving can hold.

Runs after N per-document QA runs and before index construction. Each member has already
been through anchor replay and a complete, immutable approve/quarantine decision; assembly
adds no clinical judgement and re-decides nothing. It merges, checks the properties that
only become checkable once there is more than one document, signs what it composed, and
hands the result to the same registrar every single-document release goes through.

## What assembly refuses, and why each one matters

- **A member that did not clear QA.** Composing a run that was never validated would put
  evidence into a servable release that nothing approved.
- **Colliding evidence IDs.** Two members claiming the same `evidence_id` would make the
  release's own manifest ambiguous about which record a citation resolves to. This cannot
  happen while `evidence_id` derives from a per-item run id, which is exactly why that
  derivation includes the item - so this check is a guard on that property rather than an
  expected occurrence, and it fails closed if the property ever breaks.
- **Members under different licensing revisions.** The manifest carries one
  `trust_root_registry_sha256`. Merging documents licensed under different revisions would
  silently pick one and describe the rest wrongly.
- **A member already inside another composite.** A release composed twice would be indexed
  and activated under two identities, and superseding one would leave the other serving.

## Known limit: the membership claim is not atomic with the registration

`_member_rows` checks that no member is already composed, and `_record_membership` writes
the claim, but `register_candidate` commits between them in its own session. Two concurrent
assemblies over the same members can therefore both pass the check, and the loser surfaces
a raw `IntegrityError` *after* registering a release row. The uniqueness constraint still
prevents the corrupt outcome - a QA run cannot end up in two composites - so this is a bad
error message on a race rather than a lost invariant.

Fixing it properly means one transaction spanning the claim and the registration, which is
the same restructuring that promotion-separable-from-QA requires. Recorded rather than
patched, because a half-measure here would look like atomicity without being it.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select

from app.corpus.releases import SQLCorpusReleaseRepository
from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.qa_schemas import ReleasePolicy
from app.corpus_steward.qa_service import QAService
from app.corpus_steward.registry import SQLAttestationRepository
from app.corpus_steward.release_assembly_schemas import (
    RELEASE_ASSEMBLER_NAME,
    RELEASE_ASSEMBLER_VERSION,
    ReleaseAssemblyContent,
    ReleaseAssemblyMember,
    SignedReleaseAssembly,
)
from app.corpus_steward.schemas import ArtifactKind, AttestationPurpose
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.persistence.database import Database
from app.persistence.models import CorpusQARunRow, CorpusReleaseMemberRow
from app.schemas.corpus import (
    canonical_json_bytes,
    canonical_sha256,
)

RELEASE_ASSEMBLY_PREDICATE = (
    "https://med-rag.local/attestations/release/COMPOSITE_RELEASE_ASSEMBLY"
)


class ReleaseAssemblyError(RuntimeError):
    pass


class ReleaseAssemblyService:
    def __init__(
        self,
        *,
        database: Database,
        qa: QAService,
        releases: SQLCorpusReleaseRepository,
        ledger: SQLReconciliationLedger,
        attestations: SQLAttestationRepository,
        artifacts: ImmutableStewardArtifactStore,
        signer: Ed25519Signer,
    ) -> None:
        self._database = database
        self._qa = qa
        self._releases = releases
        self._ledger = ledger
        self._attestations = attestations
        self._artifacts = artifacts
        self._signer = signer

    async def assemble(
        self,
        qa_run_ids: tuple[str, ...],
        *,
        previous_release_id: str | None = None,
        release_policy: ReleasePolicy | None = None,
    ) -> SignedReleaseAssembly:
        if not qa_run_ids:
            raise ReleaseAssemblyError("a composite release needs at least one QA run")
        if len(set(qa_run_ids)) != len(qa_run_ids):
            raise ReleaseAssemblyError("a composite release cannot compose a QA run twice")

        rows = await self._member_rows(qa_run_ids)
        members = tuple(
            ReleaseAssemblyMember(
                qa_run_id=row.qa_run_id,
                corpus_release_candidate_id=row.corpus_release_candidate_id,
                materialization_run_id=row.materialization_run_id,
                decision_batch_sha256=row.decision_batch_sha256,
                evidence_manifest_sha256=row.evidence_manifest_sha256,
                approved_count=row.approved_count,
            )
            for row in rows
        )
        release_id = self._release_id(members)
        collection = f"corpus_{release_id.lower()}"

        # Promotion happens once, in QA, over every member at the composite's release id.
        # Assembly does not build canonical evidence or sign release attestations itself:
        # two paths constructing a promoted record independently is how they stop agreeing
        # about what one is, and the composite's attestations have to cover the whole set
        # rather than one member's contents.
        bundle = await self._qa.promote_decided(
            qa_run_ids,
            release_id=release_id,
            qdrant_collection=collection,
            release_policy=release_policy,
            previous_release_id=previous_release_id,
        )

        content = ReleaseAssemblyContent(
            corpus_release_id=release_id,
            members=members,
            manifest_sha256=bundle.manifest.manifest_sha256,
            qdrant_collection=collection,
            evidence_count=len(bundle.evidence),
            assembled_at=bundle.manifest.content.created_at,
        )
        attestation = await self._attestations.record_and_verify(
            content,
            self._signer.sign(canonical_json_bytes(content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type=RELEASE_ASSEMBLY_PREDICATE,
        )
        assembly = SignedReleaseAssembly(
            content=content,
            assembly_sha256=canonical_sha256(content),
            attestation=attestation,
        )
        artifact = self._artifacts.put(
            canonical_json_bytes(assembly),
            media_type="application/vnd.med-rag.release-assembly+json",
            kind=ArtifactKind.RELEASE_ASSEMBLY,
        )
        await self._ledger.record_artifact(artifact)
        await self._record_membership(assembly)
        return assembly

    async def _member_rows(self, qa_run_ids: tuple[str, ...]) -> list[CorpusQARunRow]:
        async with self._database.session() as session:
            found = {
                row.qa_run_id: row
                for row in await session.scalars(
                    select(CorpusQARunRow).where(CorpusQARunRow.qa_run_id.in_(qa_run_ids))
                )
            }
            missing = sorted(set(qa_run_ids) - set(found))
            if missing:
                raise ReleaseAssemblyError(f"QA runs not found: {missing}")
            already = tuple(
                await session.scalars(
                    select(CorpusReleaseMemberRow).where(
                        CorpusReleaseMemberRow.qa_run_id.in_(qa_run_ids)
                    )
                )
            )
            if already:
                raise ReleaseAssemblyError(
                    "QA runs already compose a release: "
                    f"{sorted(item.qa_run_id for item in already)}"
                )
        rows = [found[qa_run_id] for qa_run_id in qa_run_ids]
        for row in rows:
            if row.state != "DECIDED":
                raise ReleaseAssemblyError(
                    f"QA run {row.qa_run_id} is {row.state}, not DECIDED; a composite "
                    "composes runs that decided without promoting"
                )
            if not row.decision_batch_sha256:
                raise ReleaseAssemblyError(
                    f"QA run {row.qa_run_id} has no sealed decision batch"
                )
        return rows

    async def _record_membership(self, assembly: SignedReleaseAssembly) -> None:
        async with self._database.session() as session:
            for member in assembly.content.members:
                session.add(
                    CorpusReleaseMemberRow(
                        corpus_release_id=assembly.content.corpus_release_id,
                        qa_run_id=member.qa_run_id,
                        assembly_sha256=assembly.assembly_sha256,
                    )
                )

    @staticmethod
    def _release_id(members: tuple[ReleaseAssemblyMember, ...]) -> str:
        """Deterministic in the membership, so re-assembling the same set is idempotent."""

        identity = ":".join(
            (
                RELEASE_ASSEMBLER_NAME,
                RELEASE_ASSEMBLER_VERSION,
                # The sealed decision batches. A member has no bundle of its own any
                # more, and the batch is what identifies what it decided.
                *sorted(member.decision_batch_sha256 for member in members),
            )
        )
        return "CR_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
