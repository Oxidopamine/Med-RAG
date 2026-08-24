from collections.abc import Collection

from app.schemas.domain import (
    CheckStatus,
    CheckType,
    Claim,
    ClaimDisposition,
    ClaimEvaluation,
    ClaimRisk,
    ClaimType,
    VerificationCheck,
)

BASE_REQUIRED_CHECKS = {
    CheckType.SOURCE_ALLOWED,
    CheckType.SOURCE_EFFECTIVE,
    CheckType.LICENSE_RENDER_ALLOWED,
    CheckType.EVIDENCE_EXISTS,
    CheckType.PROVENANCE_VALID,
    CheckType.QUOTE_EXACT,
    CheckType.SEMANTIC_ENTAILMENT,
    CheckType.ELIGIBILITY_APPLICABLE,
    CheckType.REQUIRED_EVIDENCE_COMPLETE,
    CheckType.CONTRADICTION_CLEAR,
}


class SafetyPolicy:
    def __init__(self, version: str) -> None:
        self.version = version

    def required_checks_for(self, claim: Claim) -> set[CheckType]:
        required = set(BASE_REQUIRED_CHECKS)
        if claim.risk is ClaimRisk.HIGH:
            required.add(CheckType.INDEPENDENT_SEMANTIC_VERIFIER)
        if claim.contains_numeric_content or claim.claim_type is ClaimType.THRESHOLD:
            required.update(
                {CheckType.NUMERIC_VALID, CheckType.UNIT_VALID, CheckType.OPERATOR_VALID}
            )
        if claim.claim_type is ClaimType.DOSE:
            required.update({CheckType.DOSE_VALID, CheckType.UNIT_VALID})
        if claim.claim_type is ClaimType.DURATION:
            required.add(CheckType.DURATION_VALID)
        if claim.uses_composite_table_evidence:
            required.add(CheckType.TABLE_CONTEXT_COMPLETE)
        return required


def evaluate_claim(
    claim: Claim,
    checks: Collection[VerificationCheck],
    *,
    canonical_evidence_ids: set[str],
    policy: SafetyPolicy,
) -> ClaimEvaluation:
    by_type = {check.check_type: check for check in checks}

    if not claim.candidate_evidence_ids:
        by_type[CheckType.EVIDENCE_EXISTS] = VerificationCheck(
            check_type=CheckType.EVIDENCE_EXISTS,
            status=CheckStatus.FAIL,
            reason_code="NO_CANDIDATE_EVIDENCE",
        )
    elif not set(claim.candidate_evidence_ids).issubset(canonical_evidence_ids):
        by_type[CheckType.EVIDENCE_EXISTS] = VerificationCheck(
            check_type=CheckType.EVIDENCE_EXISTS,
            status=CheckStatus.FAIL,
            reason_code="NONCANONICAL_EVIDENCE_ID",
        )

    ordered_checks: list[VerificationCheck] = []
    for check_type in sorted(policy.required_checks_for(claim), key=lambda item: item.value):
        ordered_checks.append(
            by_type.get(
                check_type,
                VerificationCheck(
                    check_type=check_type,
                    status=CheckStatus.UNRESOLVED,
                    reason_code="REQUIRED_CHECK_MISSING",
                ),
            )
        )

    blockers = [check for check in ordered_checks if check.status is not CheckStatus.PASS]
    if blockers:
        return ClaimEvaluation(
            claim=claim,
            disposition=ClaimDisposition.WITHHELD,
            checks=ordered_checks,
            policy_version=policy.version,
            withheld_reasons=[
                check.reason_code or f"{check.check_type.value}_{check.status.value}"
                for check in blockers
            ],
        )

    verified_evidence_ids = sorted(
        {
            evidence_id
            for check in ordered_checks
            for evidence_id in check.evidence_ids
            if evidence_id in canonical_evidence_ids
        }
    )
    if not set(claim.candidate_evidence_ids).issubset(verified_evidence_ids):
        return ClaimEvaluation(
            claim=claim,
            disposition=ClaimDisposition.WITHHELD,
            checks=ordered_checks,
            policy_version=policy.version,
            withheld_reasons=["EVIDENCE_NOT_VERIFIED_BY_REQUIRED_CHECKS"],
        )

    return ClaimEvaluation(
        claim=claim,
        disposition=ClaimDisposition.RENDERABLE,
        evidence_ids=verified_evidence_ids,
        checks=ordered_checks,
        policy_version=policy.version,
    )

