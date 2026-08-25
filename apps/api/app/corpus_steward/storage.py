"""Media-agnostic content-addressed storage for steward artifacts."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.corpus_steward.schemas import ArtifactKind


@dataclass(frozen=True)
class StoredStewardArtifact:
    sha256: str
    byte_size: int
    storage_key: str
    path: Path
    media_type: str
    kind: ArtifactKind


class ImmutableStewardArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def put(
        self,
        content: bytes,
        *,
        media_type: str,
        kind: ArtifactKind,
    ) -> StoredStewardArtifact:
        if not content:
            raise ValueError("immutable steward artifacts cannot be empty")
        digest = hashlib.sha256(content).hexdigest()
        storage_key = f"sha256/{digest[:2]}/{digest}.blob"
        destination = self.root / storage_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._verify_existing(destination, digest, len(content))
        else:
            temporary = destination.with_name(f".{digest}.{uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as file_handle:
                    file_handle.write(content)
                    file_handle.flush()
                    os.fsync(file_handle.fileno())
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    self._verify_existing(destination, digest, len(content))
            finally:
                temporary.unlink(missing_ok=True)
        return StoredStewardArtifact(
            sha256=digest,
            byte_size=len(content),
            storage_key=storage_key,
            path=destination,
            media_type=media_type,
            kind=kind,
        )

    def read(self, storage_key: str) -> bytes:
        return self.path_for(storage_key).read_bytes()

    def path_for(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("artifact storage key escapes the configured root")
        return candidate

    @staticmethod
    def _verify_existing(path: Path, expected_sha256: str, expected_size: int) -> None:
        if path.stat().st_size != expected_size:
            raise RuntimeError("immutable artifact path contains a different byte size")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise RuntimeError("immutable artifact path contains different content")
