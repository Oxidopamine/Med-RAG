from app.schemas.domain import (
    CheckStatus,
    CheckType,
    Claim,
    ClaimDisposition,
    ClaimRisk,
    ClaimType,
    VerificationCheck,
)
from app.verification.safety_gate import SafetyPolicy, evaluate_claim

POLICY = SafetyPolicy("test-policy")


def passing_checks(claim: Claim, evidence_id: str) -> list[VerificationCheck]:
    return [
        VerificationCheck(
            check_type=check_type,
            status=CheckStatus.PASS,
            evidence_ids=[evidence_id],
        )
        for check_type in POLICY.required_checks_for(claim)
    ]


def test_claim_without_evidence_is_withheld() -> None:
    claim = Claim(
        claim_id="C_001",
        claim_type=ClaimType.RECOMMENDATION,
        text="A proposed clinical claim",
    )

    result = evaluate_claim(
        claim,
        checks=[],
        canonical_evidence_ids=set(),
        policy=POLICY,
    )

    assert result.disposition is ClaimDisposition.WITHHELD
    assert "NO_CANDIDATE_EVIDENCE" in result.withheld_reasons


def test_noncanonical_evidence_id_is_withheld() -> None:
    claim = Claim(
        claim_id="C_002",
        claim_type=ClaimType.DEFINITION,
        text="A proposed definition",
        candidate_evidence_ids=["EV_INVENTED"],
    )

    result = evaluate_claim(
        claim,
        checks=passing_checks(claim, "EV_INVENTED"),
        canonical_evidence_ids=set(),
        policy=POLICY,
    )

    assert result.disposition is ClaimDisposition.WITHHELD
    assert "NONCANONICAL_EVIDENCE_ID" in result.withheld_reasons


def test_missing_independent_verifier_withholds_high_risk_claim() -> None:
    claim = Claim(
        claim_id="C_003",
        claim_type=ClaimType.DOSE,
        text="A proposed dose",
        candidate_evidence_ids=["EV_001"],
        risk=ClaimRisk.HIGH,
        contains_numeric_content=True,
    )
    checks = [
        item
        for item in passing_checks(claim, "EV_001")
        if item.check_type is not CheckType.INDEPENDENT_SEMANTIC_VERIFIER
    ]

    result = evaluate_claim(
        claim,
        checks=checks,
        canonical_evidence_ids={"EV_001"},
        policy=POLICY,
    )

    assert result.disposition is ClaimDisposition.WITHHELD
    assert "REQUIRED_CHECK_MISSING" in result.withheld_reasons


def test_numeric_operator_mismatch_withholds_claim() -> None:
    claim = Claim(
        claim_id="C_004",
        claim_type=ClaimType.THRESHOLD,
        text="A proposed numeric threshold",
        candidate_evidence_ids=["EV_001"],
        risk=ClaimRisk.HIGH,
        contains_numeric_content=True,
    )
    checks = passing_checks(claim, "EV_001")
    checks = [
        VerificationCheck(
            check_type=item.check_type,
            status=CheckStatus.FAIL,
            evidence_ids=item.evidence_ids,
            reason_code="OPERATOR_MISMATCH",
        )
        if item.check_type is CheckType.OPERATOR_VALID
        else item
        for item in checks
    ]

    result = evaluate_claim(
        claim,
        checks=checks,
        canonical_evidence_ids={"EV_001"},
        policy=POLICY,
    )

    assert result.disposition is ClaimDisposition.WITHHELD
    assert "OPERATOR_MISMATCH" in result.withheld_reasons


def test_fully_verified_claim_can_render() -> None:
    claim = Claim(
        claim_id="C_005",
        claim_type=ClaimType.DEFINITION,
        text="A source-supported low-risk definition",
        candidate_evidence_ids=["EV_001"],
    )

    result = evaluate_claim(
        claim,
        checks=passing_checks(claim, "EV_001"),
        canonical_evidence_ids={"EV_001"},
        policy=POLICY,
    )

    assert result.disposition is ClaimDisposition.RENDERABLE
    assert result.evidence_ids == ["EV_001"]

