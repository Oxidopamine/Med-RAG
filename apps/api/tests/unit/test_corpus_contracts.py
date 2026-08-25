import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.corpus_steward.cli import activation_schema_document, corpus_schema_document
from app.schemas.corpus import (
    ActivationDecisionContent,
    CorpusReleaseBundle,
    CorpusReleaseManifest,
    EvidenceApprovalStatus,
    EvidenceVerification,
    SignedActivationDecision,
)

FIXTURE_PATH = Path(__file__).parents[4] / "data" / "fixtures" / "corpus-release-v1.json"
SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "corpus-release-bundle-1.0.0.schema.json"
)
ACTIVATION_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "signed-activation-decision-2.0.0.schema.json"
)


def load_fixture_payload() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_frozen_corpus_release_fixture_has_stable_integrity() -> None:
    bundle = CorpusReleaseBundle.model_validate(load_fixture_payload())

    assert bundle.manifest.manifest_sha256 == (
        "7f9119c3af9d140264d4368ec113a2a1db978600bac269d772a62cfb01d7dc7a"
    )
    assert len(bundle.evidence) == 3
    assert bundle.activation_blockers() == ()


def test_checked_in_json_schema_matches_versioned_contract() -> None:
    assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == corpus_schema_document()
    assert json.loads(
        ACTIVATION_SCHEMA_PATH.read_text(encoding="utf-8")
    ) == activation_schema_document()


def test_tampered_exact_content_breaks_manifest_evidence_binding() -> None:
    payload = load_fixture_payload()
    payload["evidence"][0]["content_exact"] = "Tampered content"

    with pytest.raises(ValidationError, match="manifest evidence entries do not match"):
        CorpusReleaseBundle.model_validate(payload)


def test_tampered_manifest_digest_is_rejected() -> None:
    payload = load_fixture_payload()["manifest"]
    payload["content"]["qdrant_collection"] = "tampered_collection"

    with pytest.raises(ValidationError, match="manifest_sha256"):
        CorpusReleaseManifest.model_validate(payload)


def test_approved_evidence_requires_all_critical_invariants() -> None:
    payload = load_fixture_payload()["evidence"][0]["verification"]
    payload["checks"] = [
        check for check in payload["checks"] if check["invariant"] != "CRITICAL_FIELDS"
    ]

    with pytest.raises(ValidationError, match="CRITICAL_FIELDS"):
        EvidenceVerification.model_validate(
            {**payload, "approval_status": EvidenceApprovalStatus.APPROVED}
        )


def test_inventory_cannot_claim_complete_with_an_unaccounted_item() -> None:
    payload = load_fixture_payload()
    payload["manifest"]["content"]["inventory_snapshots"][0][
        "expected_item_ids"
    ].append("GUIDELINE_MISSING")

    with pytest.raises(ValidationError, match="complete flag"):
        CorpusReleaseBundle.model_validate(payload)


def test_activation_decision_digest_binds_release_and_index() -> None:
    decision = SignedActivationDecision.seal(
        ActivationDecisionContent(
            corpus_release_id="CR_TEST",
            manifest_sha256="a" * 64,
            release_policy_sha256="b" * 64,
            qdrant_collection="corpus_CR_TEST",
            index_point_count=3,
            index_attestation_sha256="c" * 64,
            benchmark_acceptance_sha256="e" * 64,
            decided_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        ),
        signature_sha256="d" * 64,
        signer_identity="test-control-plane",
        signing_key_id="test-key",
    )
    tampered = decision.model_dump(mode="json")
    tampered["content"]["index_point_count"] = 4

    with pytest.raises(ValidationError, match="statement_sha256"):
        SignedActivationDecision.model_validate(tampered)
