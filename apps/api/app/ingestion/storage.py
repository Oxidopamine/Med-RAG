import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pymupdf


class ArtifactValidationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class StoredArtifact:
    sha256: str
    byte_size: int
    storage_key: str
    path: Path


class ImmutablePDFStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def put(self, content: bytes, *, expected_sha256: str | None = None) -> StoredArtifact:
        sha256 = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None and sha256 != expected_sha256.lower():
            raise ArtifactValidationError(
                "SHA256_MISMATCH", "publisher PDF does not match the expected SHA-256"
            )
        if not content.startswith(b"%PDF-"):
            raise ArtifactValidationError("INVALID_PDF_HEADER", "artifact is not a PDF")
        try:
            with pymupdf.open(stream=content, filetype="pdf") as document:
                if document.needs_pass:
                    raise ArtifactValidationError(
                        "ENCRYPTED_PDF", "encrypted PDFs cannot enter the extraction pipeline"
                    )
                if document.page_count < 1:
                    raise ArtifactValidationError("EMPTY_PDF", "PDF contains no pages")
        except ArtifactValidationError:
            raise
        except Exception as error:
            raise ArtifactValidationError("MALFORMED_PDF", "PDF could not be opened") from error

        storage_key = f"{sha256[:2]}/{sha256}.pdf"
        destination = self.root / storage_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._verify_existing(destination, sha256, len(content))
        else:
            temporary = destination.with_name(f".{sha256}.{uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as file_handle:
                    file_handle.write(content)
                    file_handle.flush()
                    os.fsync(file_handle.fileno())
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    self._verify_existing(destination, sha256, len(content))
            finally:
                temporary.unlink(missing_ok=True)
        return StoredArtifact(
            sha256=sha256,
            byte_size=len(content),
            storage_key=storage_key,
            path=destination,
        )

    def path_for(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if self.root not in candidate.parents:
            raise ValueError("artifact storage key escapes the configured root")
        return candidate

    @staticmethod
    def _verify_existing(path: Path, expected_sha256: str, expected_size: int) -> None:
        if path.stat().st_size != expected_size:
            raise RuntimeError("immutable artifact path contains a different byte size")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_sha256:
            raise RuntimeError("immutable artifact path contains different content")
