"""Sealed manifests and exhaustive verification for local embedding artifacts."""

from __future__ import annotations

import hashlib
import math
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.index_schemas import EmbeddingModelReference
from app.schemas.corpus import SHA256_PATTERN, canonical_sha256
from app.schemas.domain import CanonicalModel

MODEL_ARTIFACT_CONTRACT_VERSION = "1.0.0"
MODEL_ARTIFACT_PAIR_CONTRACT_VERSION = "1.0.0"
_HASH_CHUNK_SIZE = 1024 * 1024
_WINDOWS_REPARSE_POINT = 0x400


class ModelArtifactError(RuntimeError):
    """Raised when a model artifact cannot be trusted exactly as declared."""


class ModelArtifactKind(str, Enum):
    DENSE = "DENSE"
    SPARSE = "SPARSE"
    RERANKER = "RERANKER"


class ModelArtifactFile(CanonicalModel):
    path: str = Field(min_length=1, max_length=1_000)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def validate_portable_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            "\\" in value
            or path.is_absolute()
            or value != path.as_posix()
            or any(part in ("", ".", "..") for part in path.parts)
        ):
            raise ValueError("artifact file paths must be normalized relative POSIX paths")
        return value


class ModelArtifactManifestContent(CanonicalModel):
    """Encoding contract plus an exact inventory of every local artifact byte."""

    schema_version: Literal[MODEL_ARTIFACT_CONTRACT_VERSION] = (
        MODEL_ARTIFACT_CONTRACT_VERSION
    )
    artifact_kind: ModelArtifactKind
    model_id: str = Field(min_length=1, max_length=300)
    revision: str = Field(min_length=1, max_length=300)
    dimension: int = Field(gt=0, le=2**32)
    adapter_id: str = Field(min_length=1, max_length=300)
    adapter_revision: str = Field(min_length=1, max_length=300)
    adapter_parameters: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )
    files: tuple[ModelArtifactFile, ...] = Field(min_length=1)

    @field_validator("adapter_parameters")
    @classmethod
    def validate_adapter_parameters(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        if any(isinstance(item, float) and not math.isfinite(item) for item in value.values()):
            raise ValueError("adapter parameters cannot contain non-finite numbers")
        return value

    @field_validator("files")
    @classmethod
    def validate_file_inventory(
        cls, value: tuple[ModelArtifactFile, ...]
    ) -> tuple[ModelArtifactFile, ...]:
        paths = [item.path for item in value]
        if paths != sorted(paths):
            raise ValueError("artifact file inventory must be sorted by path")
        if len(paths) != len(set(paths)):
            raise ValueError("artifact file inventory cannot repeat a path")
        folded = [path.casefold() for path in paths]
        if len(folded) != len(set(folded)):
            raise ValueError("artifact file paths cannot collide case-insensitively")
        return value


class ModelArtifactManifest(CanonicalModel):
    content: ModelArtifactManifestContent
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> ModelArtifactManifest:
        if self.artifact_sha256 != canonical_sha256(self.content):
            raise ValueError("model artifact digest does not match manifest content")
        return self

    @classmethod
    def seal(cls, content: ModelArtifactManifestContent) -> ModelArtifactManifest:
        return cls(content=content, artifact_sha256=canonical_sha256(content))

    def model_reference(self) -> EmbeddingModelReference:
        return EmbeddingModelReference(
            model_id=self.content.model_id,
            revision=self.content.revision,
            artifact_sha256=self.artifact_sha256,
        )


@dataclass(frozen=True)
class ModelArtifactVerificationLimits:
    max_file_count: int = 100_000
    max_total_bytes: int = 100 * 1024**3

    def __post_init__(self) -> None:
        if self.max_file_count <= 0:
            raise ValueError("model artifact file-count limit must be positive")
        if self.max_total_bytes <= 0:
            raise ValueError("model artifact byte limit must be positive")


@dataclass(frozen=True)
class VerifiedModelArtifact:
    """Result handed to an adapter only after complete local verification."""

    root: Path
    manifest: ModelArtifactManifest
    expected_artifact_sha256: str

    @property
    def reference(self) -> EmbeddingModelReference:
        return self.manifest.model_reference()


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    attributes = getattr(path_stat, "st_file_attributes", 0)
    return bool(attributes & _WINDOWS_REPARSE_POINT)


def _checked_root(root: Path) -> Path:
    candidate = root.absolute()
    try:
        root_stat = candidate.lstat()
    except OSError as error:
        raise ModelArtifactError(
            f"model artifact root cannot be inspected: {error.__class__.__name__}"
        ) from error
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ModelArtifactError("model artifact root must be a directory")
    if candidate.is_symlink() or _is_reparse_point(root_stat):
        raise ModelArtifactError("model artifact root cannot be a link or reparse point")
    return candidate.resolve(strict=True)


def _inventory_paths(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            raise ModelArtifactError(
                f"model artifact directory cannot be enumerated: {error.__class__.__name__}"
            ) from error
        for entry in entries:
            path = Path(entry.path)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ModelArtifactError(
                    f"model artifact entry cannot be inspected: {error.__class__.__name__}"
                ) from error
            if entry.is_symlink() or _is_reparse_point(entry_stat):
                raise ModelArtifactError(
                    f"model artifact cannot contain links or reparse points: "
                    f"{path.relative_to(root).as_posix()}"
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                pending.append(path)
            elif stat.S_ISREG(entry_stat.st_mode):
                files.append(path)
            else:
                raise ModelArtifactError(
                    f"model artifact cannot contain special files: "
                    f"{path.relative_to(root).as_posix()}"
                )
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def _hash_regular_file(path: Path, *, declared_size: int | None = None) -> tuple[int, str]:
    try:
        before = path.lstat()
        if path.is_symlink() or _is_reparse_point(before) or not stat.S_ISREG(before.st_mode):
            raise ModelArtifactError("model artifact file changed type during verification")
        if declared_size is not None and before.st_size != declared_size:
            raise ModelArtifactError("model artifact file size does not match manifest")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            if (
                opened.st_size != before.st_size
                or getattr(opened, "st_ino", None) != getattr(before, "st_ino", None)
            ):
                raise ModelArtifactError("model artifact file changed before hashing")
            while chunk := source.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
            after = os.fstat(source.fileno())
        final = path.lstat()
    except ModelArtifactError:
        raise
    except OSError as error:
        raise ModelArtifactError(
            f"model artifact file cannot be hashed: {error.__class__.__name__}"
        ) from error
    identity_before = (
        before.st_size,
        before.st_mtime_ns,
        getattr(before, "st_ino", None),
        getattr(before, "st_dev", None),
    )
    identity_after = (
        after.st_size,
        after.st_mtime_ns,
        getattr(after, "st_ino", None),
        getattr(after, "st_dev", None),
    )
    identity_final = (
        final.st_size,
        final.st_mtime_ns,
        getattr(final, "st_ino", None),
        getattr(final, "st_dev", None),
    )
    if identity_before != identity_after or identity_after != identity_final:
        raise ModelArtifactError("model artifact file changed while it was being hashed")
    return before.st_size, digest.hexdigest()


def _enforce_limits(
    files: tuple[ModelArtifactFile, ...], limits: ModelArtifactVerificationLimits
) -> None:
    if len(files) > limits.max_file_count:
        raise ModelArtifactError("model artifact exceeds the configured file-count limit")
    total_bytes = sum(item.byte_size for item in files)
    if total_bytes > limits.max_total_bytes:
        raise ModelArtifactError("model artifact exceeds the configured byte limit")


def build_model_artifact_manifest(
    root: Path,
    *,
    artifact_kind: ModelArtifactKind,
    model_id: str,
    revision: str,
    dimension: int,
    adapter_id: str,
    adapter_revision: str,
    adapter_parameters: dict[str, Any] | None = None,
    limits: ModelArtifactVerificationLimits | None = None,
) -> ModelArtifactManifest:
    """Inventory local bytes and seal the complete model/adapter encoding contract."""

    checked_root = _checked_root(root)
    effective_limits = limits or ModelArtifactVerificationLimits()
    paths = _inventory_paths(checked_root)
    if not paths:
        raise ModelArtifactError("model artifact must contain at least one regular file")
    if len(paths) > effective_limits.max_file_count:
        raise ModelArtifactError("model artifact exceeds the configured file-count limit")
    files: list[ModelArtifactFile] = []
    total_bytes = 0
    for path in paths:
        byte_size, digest = _hash_regular_file(path)
        total_bytes += byte_size
        if total_bytes > effective_limits.max_total_bytes:
            raise ModelArtifactError("model artifact exceeds the configured byte limit")
        files.append(
            ModelArtifactFile(
                path=path.relative_to(checked_root).as_posix(),
                byte_size=byte_size,
                sha256=digest,
            )
        )
    content = ModelArtifactManifestContent(
        artifact_kind=artifact_kind,
        model_id=model_id,
        revision=revision,
        dimension=dimension,
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        adapter_parameters=adapter_parameters or {},
        files=tuple(files),
    )
    return ModelArtifactManifest.seal(content)


def verify_model_artifact(
    root: Path,
    manifest: ModelArtifactManifest,
    *,
    expected_artifact_sha256: str,
    expected_kind: ModelArtifactKind | None = None,
    limits: ModelArtifactVerificationLimits | None = None,
) -> VerifiedModelArtifact:
    """Exhaustively match a local tree to a sealed, encoding-bound manifest."""

    checked_root = _checked_root(root)
    effective_limits = limits or ModelArtifactVerificationLimits()
    declared = manifest.content.files
    _enforce_limits(declared, effective_limits)
    if expected_artifact_sha256 != manifest.artifact_sha256:
        raise ModelArtifactError(
            "model artifact manifest does not match the independently pinned digest"
        )
    if expected_kind is not None and manifest.content.artifact_kind is not expected_kind:
        raise ModelArtifactError(
            f"expected a {expected_kind.value.lower()} model artifact, got "
            f"{manifest.content.artifact_kind.value.lower()}"
        )

    actual_paths = _inventory_paths(checked_root)
    actual_relative = tuple(path.relative_to(checked_root).as_posix() for path in actual_paths)
    declared_relative = tuple(item.path for item in declared)
    if actual_relative != declared_relative:
        missing = sorted(set(declared_relative) - set(actual_relative))
        unexpected = sorted(set(actual_relative) - set(declared_relative))
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ModelArtifactError("model artifact inventory mismatch: " + "; ".join(details))

    for path, wanted in zip(actual_paths, declared, strict=True):
        byte_size, digest = _hash_regular_file(path, declared_size=wanted.byte_size)
        if byte_size != wanted.byte_size or digest != wanted.sha256:
            raise ModelArtifactError(
                f"model artifact digest mismatch for file: {wanted.path}"
            )

    final_relative = tuple(
        path.relative_to(checked_root).as_posix() for path in _inventory_paths(checked_root)
    )
    if final_relative != declared_relative:
        raise ModelArtifactError("model artifact inventory changed during verification")
    return VerifiedModelArtifact(
        root=checked_root,
        manifest=manifest,
        expected_artifact_sha256=expected_artifact_sha256,
    )


class ModelArtifactRole(str, Enum):
    """The asymmetric side a paired artifact is allowed to encode."""

    DOCUMENT = "DOCUMENT"
    QUERY = "QUERY"


class ModelArtifactPairMember(CanonicalModel):
    """One role-bound half of a dual-encoder candidate, sealed in full."""

    role: ModelArtifactRole
    manifest: ModelArtifactManifest


class ModelArtifactPairManifestContent(CanonicalModel):
    """Two asymmetric artifact roots bound as exactly one candidate identity.

    Each member carries a complete, independently sealed single-artifact manifest, so
    the pair digest transitively covers every byte of both roots. Behavior-affecting
    parameters live only on the pair: a half sealed on its own declares no parameters
    and therefore cannot configure any adapter by itself.
    """

    schema_version: Literal[MODEL_ARTIFACT_PAIR_CONTRACT_VERSION] = (
        MODEL_ARTIFACT_PAIR_CONTRACT_VERSION
    )
    artifact_kind: ModelArtifactKind
    model_id: str = Field(min_length=1, max_length=300)
    revision: str = Field(min_length=1, max_length=300)
    dimension: int = Field(gt=0, le=2**32)
    adapter_id: str = Field(min_length=1, max_length=300)
    adapter_revision: str = Field(min_length=1, max_length=300)
    adapter_parameters: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )
    members: tuple[ModelArtifactPairMember, ...] = Field(min_length=2, max_length=2)

    @field_validator("adapter_parameters")
    @classmethod
    def validate_adapter_parameters(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        if any(isinstance(item, float) and not math.isfinite(item) for item in value.values()):
            raise ValueError("adapter parameters cannot contain non-finite numbers")
        return value

    @model_validator(mode="after")
    def validate_pair_binding(self) -> ModelArtifactPairManifestContent:
        roles = tuple(member.role for member in self.members)
        if roles != (ModelArtifactRole.DOCUMENT, ModelArtifactRole.QUERY):
            raise ValueError(
                "an artifact pair must list exactly one document and one query member, "
                "sorted by role"
            )
        digests = {member.manifest.artifact_sha256 for member in self.members}
        if len(digests) != len(self.members):
            raise ValueError("an artifact pair cannot bind the same artifact twice")
        for member in self.members:
            content = member.manifest.content
            side = member.role.value.lower()
            if content.artifact_kind is not self.artifact_kind:
                raise ValueError(f"the {side} member must declare the paired artifact kind")
            if content.dimension != self.dimension:
                raise ValueError(f"the {side} member must declare the paired dimension")
            if (
                content.adapter_id != self.adapter_id
                or content.adapter_revision != self.adapter_revision
            ):
                raise ValueError(f"the {side} member must declare the paired adapter")
            if content.adapter_parameters:
                raise ValueError(
                    f"the {side} member cannot carry parameters; a paired candidate seals "
                    "every behavior-affecting parameter on the pair"
                )
        if self.revision != pair_revision(self.members):
            raise ValueError(
                "an artifact pair revision must be the derived composite of its member "
                "revisions"
            )
        return self


class ModelArtifactPairManifest(CanonicalModel):
    content: ModelArtifactPairManifestContent
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> ModelArtifactPairManifest:
        if self.artifact_sha256 != canonical_sha256(self.content):
            raise ValueError("model artifact pair digest does not match manifest content")
        return self

    @classmethod
    def seal(cls, content: ModelArtifactPairManifestContent) -> ModelArtifactPairManifest:
        return cls(content=content, artifact_sha256=canonical_sha256(content))

    def member(self, role: ModelArtifactRole) -> ModelArtifactPairMember:
        for member in self.content.members:
            if member.role is role:
                return member
        raise ModelArtifactError(f"artifact pair has no {role.value.lower()} member")

    def model_reference(self) -> EmbeddingModelReference:
        return EmbeddingModelReference(
            model_id=self.content.model_id,
            revision=self.content.revision,
            artifact_sha256=self.artifact_sha256,
        )


@dataclass(frozen=True)
class VerifiedModelArtifactPair:
    """Both halves verified, handed to an adapter as one indivisible identity.

    Exposes the same ``manifest``/``reference`` surface a single verified artifact
    does, so a paired adapter drops into every consumer that pins vector identity from
    ``manifest.content`` without those consumers learning about roles.
    """

    manifest: ModelArtifactPairManifest
    expected_artifact_sha256: str
    document: VerifiedModelArtifact
    query: VerifiedModelArtifact

    @property
    def reference(self) -> EmbeddingModelReference:
        return self.manifest.model_reference()

    def member(self, role: ModelArtifactRole) -> VerifiedModelArtifact:
        return self.query if role is ModelArtifactRole.QUERY else self.document


def pair_revision(members: tuple[ModelArtifactPairMember, ...]) -> str:
    """Derive the one immutable revision string a paired candidate is known by."""

    return ";".join(
        f"{member.role.value.lower()}={member.manifest.content.revision}"
        for member in members
    )


def build_model_artifact_pair_manifest(
    *,
    query: ModelArtifactManifest,
    document: ModelArtifactManifest,
    model_id: str,
    adapter_parameters: dict[str, Any] | None = None,
) -> ModelArtifactPairManifest:
    """Bind two already-sealed artifact manifests into one paired candidate identity.

    Kind, dimension, and adapter identity are read from the members rather than
    restated, so a pair cannot claim an encoding contract its halves do not carry.
    """

    members = (
        ModelArtifactPairMember(role=ModelArtifactRole.DOCUMENT, manifest=document),
        ModelArtifactPairMember(role=ModelArtifactRole.QUERY, manifest=query),
    )
    content = ModelArtifactPairManifestContent(
        artifact_kind=document.content.artifact_kind,
        model_id=model_id,
        revision=pair_revision(members),
        dimension=document.content.dimension,
        adapter_id=document.content.adapter_id,
        adapter_revision=document.content.adapter_revision,
        adapter_parameters=adapter_parameters or {},
        members=members,
    )
    return ModelArtifactPairManifest.seal(content)


def verify_model_artifact_pair(
    *,
    query_root: Path,
    document_root: Path,
    manifest: ModelArtifactPairManifest,
    expected_artifact_sha256: str,
    expected_kind: ModelArtifactKind | None = None,
    limits: ModelArtifactVerificationLimits | None = None,
) -> VerifiedModelArtifactPair:
    """Exhaustively verify both roots against the one digest that pins the pair."""

    if expected_artifact_sha256 != manifest.artifact_sha256:
        raise ModelArtifactError(
            "model artifact pair manifest does not match the independently pinned digest"
        )
    content = manifest.content
    if expected_kind is not None and content.artifact_kind is not expected_kind:
        raise ModelArtifactError(
            f"expected a {expected_kind.value.lower()} model artifact pair, got "
            f"{content.artifact_kind.value.lower()}"
        )
    roots = {
        ModelArtifactRole.DOCUMENT: document_root,
        ModelArtifactRole.QUERY: query_root,
    }
    verified: dict[ModelArtifactRole, VerifiedModelArtifact] = {}
    for member in content.members:
        verified[member.role] = verify_model_artifact(
            roots[member.role],
            member.manifest,
            expected_artifact_sha256=member.manifest.artifact_sha256,
            expected_kind=content.artifact_kind,
            limits=limits,
        )
    if verified[ModelArtifactRole.DOCUMENT].root == verified[ModelArtifactRole.QUERY].root:
        raise ModelArtifactError(
            "an artifact pair cannot resolve both roles to the same artifact root"
        )
    return VerifiedModelArtifactPair(
        manifest=manifest,
        expected_artifact_sha256=expected_artifact_sha256,
        document=verified[ModelArtifactRole.DOCUMENT],
        query=verified[ModelArtifactRole.QUERY],
    )
