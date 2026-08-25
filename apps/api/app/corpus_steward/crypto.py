"""Ed25519 signing primitives for detached steward attestations."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.corpus_steward.schemas import SignatureEnvelope


class SignatureVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class Ed25519Signer:
    key_id: str
    signer_identity: str
    private_key: Ed25519PrivateKey

    @classmethod
    def from_pem(
        cls,
        path: Path,
        *,
        key_id: str,
        signer_identity: str,
    ) -> Ed25519Signer:
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError("the signing key must be an Ed25519 private key")
        return cls(key_id=key_id, signer_identity=signer_identity, private_key=loaded)

    def sign(self, statement: bytes) -> SignatureEnvelope:
        signature = self.private_key.sign(statement)
        return SignatureEnvelope(
            key_id=self.key_id,
            signer_identity=self.signer_identity,
            statement_sha256=hashlib.sha256(statement).hexdigest(),
            signature_base64=base64.b64encode(signature).decode("ascii"),
            signature_sha256=hashlib.sha256(signature).hexdigest(),
        )

    def public_key_pem(self) -> str:
        return self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")


def verify_ed25519_signature(
    *,
    public_key_pem: str,
    statement: bytes,
    envelope: SignatureEnvelope,
) -> None:
    if hashlib.sha256(statement).hexdigest() != envelope.statement_sha256:
        raise SignatureVerificationError("attestation statement digest mismatch")
    try:
        loaded = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    except (TypeError, ValueError) as error:
        raise SignatureVerificationError("registered public key is invalid") from error
    if not isinstance(loaded, Ed25519PublicKey):
        raise SignatureVerificationError("registered key is not Ed25519")
    try:
        loaded.verify(base64.b64decode(envelope.signature_base64), statement)
    except InvalidSignature as error:
        raise SignatureVerificationError("Ed25519 signature verification failed") from error


def generate_ed25519_key_pair() -> tuple[bytes, bytes]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem
