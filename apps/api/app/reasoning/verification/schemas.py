"""Contracts for deterministic claim verification.

Deterministic validators answer one narrow question: does every checkable surface
feature of a claim - its numbers, its units, its comparison operators, its quoted
spans, and the provenance it cites - actually occur in the evidence that claim cites?
They do not decide whether the evidence *entails* the claim. That is the separate
calibrated semantic verifier, and nothing in this package is a substitute for it. A
claim can pass every validator here and still be clinically wrong, because reusing the
right numbers in the wrong relation is invisible to a surface check.

What these validators do buy is the failure mode a surface check catches completely: a
number, unit, threshold direction, or quotation that never appeared in the cited
evidence at all. That is fabrication rather than misreading, and it is decidable
without a model.

The outcome is three-state on purpose. A validator that cannot read the evidence it
was asked to check reports ``UNRESOLVED`` rather than passing, because the case being
guarded against - a fabricated dose surviving because the cited passage was
unreadable - is exactly what a two-state design would render.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VerificationStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNRESOLVED = "UNRESOLVED"


class ValidatorName(str, Enum):
    NUMERIC = "NUMERIC"
    UNIT = "UNIT"
    OPERATOR = "OPERATOR"
    QUOTE = "QUOTE"
    PROVENANCE = "PROVENANCE"


@dataclass(frozen=True)
class VerifiableEvidence:
    """One cited evidence record as the deterministic validators see it.

    ``exact_text`` is ``None`` for evidence the release forbids rendering. That is not
    an error and not a pass: the content validators have nothing to read, so they
    report ``UNRESOLVED`` and the claim is withheld.
    """

    evidence_id: str
    exact_text: str | None
    render_allowed: bool = True
    approval_status: str = "APPROVED"
    lifecycle_status: str = "ACTIVE"
    locator_count: int = 1

    @property
    def readable_text(self) -> str | None:
        if not self.render_allowed:
            return None
        if self.exact_text is None or not self.exact_text.strip():
            return None
        return self.exact_text


@dataclass(frozen=True)
class AtomicClaim:
    """One independently checkable assertion carved out of a model claim.

    Every atomic claim inherits the whole citation set of its parent, because the model
    cites per claim rather than per sentence. That is deliberately generous to the
    claim: it may draw support from any cited passage. The strictness lives in the
    combination rule - the parent is supported only if every atomic claim is.
    """

    index: int
    text: str


@dataclass(frozen=True)
class ValidatorFinding:
    validator: ValidatorName
    status: VerificationStatus
    detail: str
    atomic_index: int | None = None


@dataclass(frozen=True)
class ClaimVerification:
    status: VerificationStatus
    atomic_claims: tuple[AtomicClaim, ...]
    findings: tuple[ValidatorFinding, ...]

    @property
    def failures(self) -> tuple[ValidatorFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.status is not VerificationStatus.SUPPORTED
        )

    def summary(self) -> str:
        """A single line naming why a claim was withheld, for operator diagnostics."""

        failures = self.failures
        if not failures:
            return "all deterministic validators passed"
        return "; ".join(
            f"{finding.validator.value}: {finding.detail}" for finding in failures
        )


def combine(statuses: tuple[VerificationStatus, ...]) -> VerificationStatus:
    """Fold validator outcomes into one claim outcome.

    A positive mismatch outranks an unreadable check, which outranks a pass. Only a
    claim where every validator passed is ``SUPPORTED``; nothing here can promote an
    outcome, only hold it down.
    """

    if VerificationStatus.UNSUPPORTED in statuses:
        return VerificationStatus.UNSUPPORTED
    if VerificationStatus.UNRESOLVED in statuses:
        return VerificationStatus.UNRESOLVED
    return VerificationStatus.SUPPORTED
