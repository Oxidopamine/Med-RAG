"""Run the deterministic validators over one claim and fold the result.

The verifier is a pure function of the claim text and the cited evidence: same inputs,
same findings, no network, no model, no state. That is what makes it usable as a gate
rather than as a signal - a gate whose answer can change between two runs on the same
release is not a gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.reasoning.verification.claim_planning import plan_atomic_claims
from app.reasoning.verification.schemas import (
    ClaimVerification,
    ValidatorFinding,
    ValidatorName,
    VerifiableEvidence,
    VerificationStatus,
    combine,
)
from app.reasoning.verification.validators import VALIDATORS


class DeterministicClaimVerifier:
    """Decide whether a claim's checkable surface features hold against its citations."""

    def verify(
        self,
        claim_text: str,
        evidence_ids: Sequence[str],
        evidence: Mapping[str, VerifiableEvidence],
    ) -> ClaimVerification:
        atomic_claims = plan_atomic_claims(claim_text)

        unknown = [item for item in evidence_ids if item not in evidence]
        if unknown:
            # Upstream grounding already rejects citations outside the retrieved set, so
            # reaching here means the two views of the retrieval disagree. Trusting
            # either one over the other is not available, so the claim is withheld.
            return ClaimVerification(
                status=VerificationStatus.UNSUPPORTED,
                atomic_claims=atomic_claims,
                findings=(
                    ValidatorFinding(
                        validator=ValidatorName.PROVENANCE,
                        status=VerificationStatus.UNSUPPORTED,
                        detail=(
                            "cited evidence is absent from the verification set: "
                            + ", ".join(sorted(unknown))
                        ),
                    ),
                ),
            )

        cited = tuple(evidence[item] for item in dict.fromkeys(evidence_ids))
        findings: list[ValidatorFinding] = []
        for atomic in atomic_claims:
            for validator in VALIDATORS:
                findings.extend(validator(atomic, cited))

        status = combine(tuple(finding.status for finding in findings))
        return ClaimVerification(
            status=status, atomic_claims=atomic_claims, findings=tuple(findings)
        )
