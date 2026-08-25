from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.crypto import SignatureVerificationError, verify_ed25519_signature
from app.corpus_steward.schemas import (
    AttestationPurpose,
    BenchmarkAttestationPurpose,
    BenchmarkAttestationReference,
    SignatureEnvelope,
    TrustRootDefinition,
    VerifiedAttestationReference,
)
from app.persistence.database import Database
from app.persistence.models import (
    CryptographicAttestationRow,
    StewardSigningKeyRow,
    TrustRootRevisionRow,
    TrustRootRow,
)
from app.schemas.corpus import canonical_json_bytes
from app.schemas.domain import CanonicalModel, utc_now


class StewardRegistryError(RuntimeError):
    pass


class StewardRegistryConflictError(StewardRegistryError):
    pass


class StewardRegistryNotFoundError(StewardRegistryError):
    pass


class AttestationVerificationError(StewardRegistryError):
    pass


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class SQLTrustRootRegistry:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def register(
        self, definition: TrustRootDefinition, *, replace: bool = False
    ) -> TrustRootDefinition:
        now = utc_now()
        async with self._database.session() as session:
            row = await session.get(TrustRootRow, definition.trust_root_id)
            if row is not None:
                if row.definition_sha256 == definition.sha256:
                    await self._ensure_revision(session, definition, now)
                    return TrustRootDefinition.model_validate(row.definition)
                if not replace:
                    raise StewardRegistryConflictError(
                        "trust-root ID already has a different definition; use an explicit replace"
                    )
                self._apply_definition(row, definition)
                row.updated_at = now
                await self._ensure_revision(session, definition, now)
                return definition
            row = TrustRootRow(
                trust_root_id=definition.trust_root_id,
                created_at=now,
                updated_at=now,
                last_reconciled_at=None,
            )
            self._apply_definition(row, definition)
            session.add(row)
            await session.flush()
            await self._ensure_revision(session, definition, now)
        return definition

    async def get(self, trust_root_id: str) -> TrustRootDefinition:
        async with self._database.session() as session:
            row = await session.get(TrustRootRow, trust_root_id)
            if row is None:
                raise StewardRegistryNotFoundError(f"trust root not found: {trust_root_id}")
            definition = TrustRootDefinition.model_validate(row.definition)
            if definition.sha256 != row.definition_sha256:
                raise StewardRegistryError("stored trust-root definition digest is inconsistent")
            if not definition.enabled:
                raise StewardRegistryError(f"trust root is disabled: {trust_root_id}")
            return definition

    async def get_revision(
        self, definition_sha256: str, *, trust_root_id: str | None = None
    ) -> TrustRootDefinition:
        async with self._database.session() as session:
            row = await session.get(TrustRootRevisionRow, definition_sha256)
            if row is None:
                raise StewardRegistryNotFoundError(
                    f"trust-root revision not found: {definition_sha256}"
                )
            if trust_root_id is not None and row.trust_root_id != trust_root_id:
                raise StewardRegistryError("trust-root revision belongs to another root")
            definition = TrustRootDefinition.model_validate(row.definition)
            if definition.sha256 != row.definition_sha256:
                raise StewardRegistryError("stored trust-root revision digest is inconsistent")
            return definition

    @staticmethod
    def _apply_definition(row: TrustRootRow, definition: TrustRootDefinition) -> None:
        payload = definition.model_dump(mode="json")
        row.publisher_id = definition.publisher_id
        row.publisher_name = definition.publisher_name
        row.allowed_domains = list(definition.allowed_domains)
        row.jurisdictions = list(definition.jurisdictions)
        row.product_families = list(definition.product_families)
        row.licensing_policy = (
            definition.licensing_policy.model_dump(mode="json")
            if definition.licensing_policy is not None
            else {
                "mode": "PER_ASSET",
                "assets": [
                    item.model_dump(mode="json") for item in definition.asset_licensing
                ],
            }
        )
        row.polling_interval_seconds = definition.polling_interval_seconds
        row.connector_name = definition.connector_name
        row.connector_version = definition.connector_version
        row.connector_config = definition.connector_config
        row.trusted_stage_key_ids = list(definition.trusted_stage_key_ids)
        row.definition = payload
        row.definition_sha256 = definition.sha256
        row.enabled = definition.enabled

    @staticmethod
    async def _ensure_revision(session, definition: TrustRootDefinition, now) -> None:
        existing = await session.get(TrustRootRevisionRow, definition.sha256)
        if existing is not None:
            if existing.trust_root_id != definition.trust_root_id:
                raise StewardRegistryConflictError(
                    "trust-root definition digest belongs to another trust root"
                )
            return
        session.add(
            TrustRootRevisionRow(
                definition_sha256=definition.sha256,
                trust_root_id=definition.trust_root_id,
                definition=definition.model_dump(mode="json"),
                registered_at=now,
            )
        )


class SQLAttestationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def register_key(
        self,
        *,
        key_id: str,
        signer_identity: str,
        public_key_pem: str,
        purposes: Iterable[AttestationPurpose | BenchmarkAttestationPurpose],
    ) -> None:
        purpose_values = sorted({purpose.value for purpose in purposes})
        if not purpose_values:
            raise ValueError("a signing key must have at least one purpose")
        try:
            loaded = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
        except (TypeError, ValueError) as error:
            raise ValueError("public key is not valid PEM") from error
        if not isinstance(loaded, Ed25519PublicKey):
            raise ValueError("steward signing keys must be Ed25519")
        now = utc_now()
        try:
            async with self._database.session() as session:
                existing = await session.get(StewardSigningKeyRow, key_id)
                if existing is not None:
                    same = (
                        existing.signer_identity == signer_identity
                        and existing.public_key_pem == public_key_pem
                        and existing.purposes == purpose_values
                    )
                    if not same:
                        raise StewardRegistryConflictError(
                            "signing key ID already identifies different key material or policy"
                        )
                    return
                session.add(
                    StewardSigningKeyRow(
                        key_id=key_id,
                        algorithm="ED25519",
                        signer_identity=signer_identity,
                        purposes=purpose_values,
                        public_key_pem=public_key_pem,
                        enabled=True,
                        created_at=now,
                    )
                )
        except IntegrityError as error:
            raise StewardRegistryConflictError("signing-key registry conflict") from error

    async def record_and_verify(
        self,
        statement: CanonicalModel,
        envelope: SignatureEnvelope,
        *,
        purpose: AttestationPurpose | BenchmarkAttestationPurpose,
        predicate_type: str,
    ) -> VerifiedAttestationReference | BenchmarkAttestationReference:
        statement_bytes = canonical_json_bytes(statement)
        now = utc_now()
        async with self._database.session() as session:
            key = await session.get(StewardSigningKeyRow, envelope.key_id)
            if key is None or not key.enabled:
                raise AttestationVerificationError("signing key is not enabled or registered")
            if purpose.value not in key.purposes:
                raise AttestationVerificationError(
                    f"signing key is not authorized for {purpose.value} attestations"
                )
            if key.signer_identity != envelope.signer_identity:
                raise AttestationVerificationError("signer identity does not match key registry")
            try:
                verify_ed25519_signature(
                    public_key_pem=key.public_key_pem,
                    statement=statement_bytes,
                    envelope=envelope,
                )
            except SignatureVerificationError as error:
                raise AttestationVerificationError(str(error)) from error

            existing = await session.scalar(
                select(CryptographicAttestationRow).where(
                    CryptographicAttestationRow.statement_sha256
                    == envelope.statement_sha256,
                    CryptographicAttestationRow.signature_sha256
                    == envelope.signature_sha256,
                    CryptographicAttestationRow.purpose == purpose.value,
                )
            )
            if existing is not None:
                if (
                    existing.signing_key_id != envelope.key_id
                    or existing.predicate_type != predicate_type
                ):
                    raise StewardRegistryConflictError(
                        "attestation digest already has different provenance"
                    )
                return self._reference(existing)

            row = CryptographicAttestationRow(
                attestation_id=f"ATT_{uuid4().hex}",
                purpose=purpose.value,
                predicate_type=predicate_type,
                statement_sha256=envelope.statement_sha256,
                signature_sha256=envelope.signature_sha256,
                signature_base64=envelope.signature_base64,
                signing_key_id=envelope.key_id,
                signer_identity=envelope.signer_identity,
                statement=statement.model_dump(mode="json"),
                verified_at=now,
            )
            session.add(row)
            await session.flush()
            return self._reference(row)

    async def verify_existing_reference(
        self,
        statement: CanonicalModel,
        *,
        purpose: AttestationPurpose | BenchmarkAttestationPurpose,
        predicate_type: str,
        statement_sha256: str,
        signature_sha256: str,
        signing_key_id: str,
        signer_identity: str,
    ) -> VerifiedAttestationReference | BenchmarkAttestationReference:
        async with self._database.session() as session:
            row = await session.scalar(
                select(CryptographicAttestationRow).where(
                    CryptographicAttestationRow.statement_sha256 == statement_sha256,
                    CryptographicAttestationRow.signature_sha256 == signature_sha256,
                    CryptographicAttestationRow.purpose == purpose.value,
                )
            )
            if row is None:
                raise AttestationVerificationError("detached signature is not registered")
            if (
                row.signing_key_id != signing_key_id
                or row.signer_identity != signer_identity
                or row.predicate_type != predicate_type
            ):
                raise AttestationVerificationError("attestation provenance does not match")
            key = await session.get(StewardSigningKeyRow, signing_key_id)
            if key is None or not key.enabled or purpose.value not in key.purposes:
                raise AttestationVerificationError("attestation key is no longer trusted")
            try:
                envelope = SignatureEnvelope(
                    key_id=row.signing_key_id,
                    signer_identity=row.signer_identity,
                    statement_sha256=row.statement_sha256,
                    signature_base64=row.signature_base64,
                    signature_sha256=row.signature_sha256,
                )
                verify_ed25519_signature(
                    public_key_pem=key.public_key_pem,
                    statement=canonical_json_bytes(statement),
                    envelope=envelope,
                )
            except (SignatureVerificationError, ValidationError) as error:
                raise AttestationVerificationError(str(error)) from error
            return self._reference(row)

    async def get_reference(
        self, attestation_id: str
    ) -> VerifiedAttestationReference | BenchmarkAttestationReference:
        async with self._database.session() as session:
            row = await session.get(CryptographicAttestationRow, attestation_id)
            if row is None:
                raise StewardRegistryNotFoundError(f"attestation not found: {attestation_id}")
            return self._reference(row)

    @staticmethod
    def _reference(
        row: CryptographicAttestationRow,
    ) -> VerifiedAttestationReference | BenchmarkAttestationReference:
        if row.purpose == BenchmarkAttestationPurpose.BENCHMARK_ACCEPTANCE.value:
            return BenchmarkAttestationReference(
                attestation_id=row.attestation_id,
                purpose=BenchmarkAttestationPurpose(row.purpose),
                predicate_type=row.predicate_type,
                statement_sha256=row.statement_sha256,
                signature_sha256=row.signature_sha256,
                signer_identity=row.signer_identity,
                signing_key_id=row.signing_key_id,
                verified_at=_as_utc(row.verified_at),
            )
        return VerifiedAttestationReference(
            attestation_id=row.attestation_id,
            purpose=AttestationPurpose(row.purpose),
            predicate_type=row.predicate_type,
            statement_sha256=row.statement_sha256,
            signature_sha256=row.signature_sha256,
            signer_identity=row.signer_identity,
            signing_key_id=row.signing_key_id,
            verified_at=_as_utc(row.verified_at),
        )


def statement_payload(value: CanonicalModel) -> dict[str, Any]:
    return value.model_dump(mode="json")
