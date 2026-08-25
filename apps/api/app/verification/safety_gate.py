from collections import defaultdict
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

EVIDENCE_SCOPED_CHECKS = {
    CheckType.SOURCE_ALLOWED,
    CheckType.SOURCE_EFFECTIVE,
    CheckType.LICENSE_RENDER_ALLOWED,
    CheckType.EVIDENCE_EXISTS,
    CheckType.PROVENANCE_VALID,
    CheckType.QUOTE_EXACT,
    CheckType.SEMANTIC_ENTAILMENT,
    CheckType.INDEPENDENT_SEMANTIC_VERIFIER,
    CheckType.NUMERIC_VALID,
    CheckType.UNIT_VALID,
    CheckType.OPERATOR_VALID,
    CheckType.DURATION_VALID,
    CheckType.DOSE_VALID,
    CheckType.TABLE_CONTEXT_COMPLETE,
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
    by_type: dict[CheckType, list[VerificationCheck]] = defaultdict(list)
    for check in checks:
        by_type[check.check_type].append(check)

    forced_checks: dict[CheckType, VerificationCheck] = {}

    if not claim.candidate_evidence_ids:
        forced_checks[CheckType.EVIDENCE_EXISTS] = VerificationCheck(
            check_type=CheckType.EVIDENCE_EXISTS,
            status=CheckStatus.FAIL,
            reason_code="NO_CANDIDATE_EVIDENCE",
        )
    elif not set(claim.candidate_evidence_ids).issubset(canonical_evidence_ids):
        forced_checks[CheckType.EVIDENCE_EXISTS] = VerificationCheck(
            check_type=CheckType.EVIDENCE_EXISTS,
            status=CheckStatus.FAIL,
            reason_code="NONCANONICAL_EVIDENCE_ID",
        )

    ordered_checks: list[VerificationCheck] = []
    for check_type in sorted(policy.required_checks_for(claim), key=lambda item: item.value):
        if check_type in forced_checks:
            ordered_checks.append(forced_checks[check_type])
            continue

        provided = by_type.get(check_type, [])
        if not provided:
            ordered_checks.append(
                VerificationCheck(
                    check_type=check_type,
                    status=CheckStatus.UNRESOLVED,
                    reason_code="REQUIRED_CHECK_MISSING",
                )
            )
        elif len(provided) > 1:
            ordered_checks.append(
                VerificationCheck(
                    check_type=check_type,
                    status=CheckStatus.FAIL,
                    reason_code="DUPLICATE_REQUIRED_CHECK",
                    details={"received": len(provided)},
                )
            )
        else:
            ordered_checks.append(provided[0])

    candidate_evidence_ids = set(claim.candidate_evidence_ids)
    coverage_checked: list[VerificationCheck] = []
    for check in ordered_checks:
        missing_evidence_ids = candidate_evidence_ids - set(check.evidence_ids)
        if (
            check.status is CheckStatus.PASS
            and check.check_type in EVIDENCE_SCOPED_CHECKS
            and missing_evidence_ids
        ):
            coverage_checked.append(
                VerificationCheck(
                    check_type=check.check_type,
                    status=CheckStatus.FAIL,
                    evidence_ids=check.evidence_ids,
                    reason_code="EVIDENCE_CHECK_COVERAGE_INCOMPLETE",
                    details={
                        **check.details,
                        "missing_evidence_ids": sorted(missing_evidence_ids),
                    },
                )
            )
        else:
            coverage_checked.append(check)
    ordered_checks = coverage_checked

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

    return ClaimEvaluation(
        claim=claim,
        disposition=ClaimDisposition.RENDERABLE,
        evidence_ids=sorted(candidate_evidence_ids),
        checks=ordered_checks,
        policy_version=policy.version,
    )
