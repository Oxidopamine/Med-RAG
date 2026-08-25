"""Bounded, non-extracting parser for FHIR NPM ``package.tgz`` artifacts."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.corpus_steward.structured_input_schemas import (
    FHIRDependencyPackageManifest,
)
from app.corpus_steward.structured_schemas import (
    FHIRImplementationGuideMetadata,
    FHIRPackageDependency,
    FHIRPackageManifest,
    FHIRResourceIndexEntry,
)
from app.schemas.corpus import canonical_sha256


class FHIRPackageValidationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class FHIRPackageLimits:
    max_entries: int = 10_000
    max_total_uncompressed_bytes: int = 512 * 1024 * 1024
    max_member_bytes: int = 32 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_json_depth: int = 100

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_entries,
            self.max_total_uncompressed_bytes,
            self.max_member_bytes,
            self.max_json_depth,
        )
        if any(value < 1 for value in integer_limits):
            raise ValueError("FHIR package integer limits must be positive")
        if self.max_compression_ratio < 1:
            raise ValueError("FHIR package compression ratio limit must be at least one")


@dataclass(frozen=True)
class ParsedFHIRPackage:
    manifest: FHIRPackageManifest
    implementation_guide: FHIRImplementationGuideMetadata
    resources: tuple[FHIRResourceIndexEntry, ...]
    resource_type_counts: dict[str, int]
    resource_inventory_sha256: str
    missing_declared_resources: tuple[str, ...]
    undeclared_resources: tuple[str, ...]
    declared_strings: frozenset[str]


class FHIRPackageParser:
    def __init__(self, limits: FHIRPackageLimits | None = None) -> None:
        self._limits = limits or FHIRPackageLimits()

    def parse(self, content: bytes) -> ParsedFHIRPackage:
        json_members = self._read_json_members(content)
        manifest_raw, manifest_payload = self._required_member(
            json_members, "package/package.json"
        )
        manifest = self._manifest(manifest_payload, manifest_raw)
        implementation_guides = [
            (path, raw, payload)
            for path, (raw, payload) in json_members.items()
            if path.startswith("package/ImplementationGuide-")
            and path.count("/") == 1
            and payload.get("resourceType") == "ImplementationGuide"
        ]
        if len(implementation_guides) != 1:
            raise FHIRPackageValidationError(
                "IMPLEMENTATION_GUIDE_COUNT",
                "FHIR package must contain exactly one root ImplementationGuide",
            )
        _, ig_raw, ig_payload = implementation_guides[0]
        declared = self._declared_resources(ig_payload)
        declared_strings = frozenset(self._all_strings(ig_payload))
        resources: list[FHIRResourceIndexEntry] = []
        seen_resource_keys: set[str] = set()
        for path, (raw, payload) in sorted(json_members.items()):
            if (
                path in {"package/package.json", "package/.index.json"}
                or path.startswith("package/other/")
            ):
                continue
            resource_type = payload.get("resourceType")
            resource_id = payload.get("id")
            if not isinstance(resource_type, str) or not isinstance(resource_id, str):
                raise FHIRPackageValidationError(
                    "FHIR_RESOURCE_IDENTITY",
                    f"FHIR JSON requires resourceType and id: {path}",
                )
            is_example = path.startswith("package/example/")
            reference = f"{resource_type}/{resource_id}"
            resource_key = f"example:{reference}" if is_example else reference
            if resource_key in seen_resource_keys:
                raise FHIRPackageValidationError(
                    "FHIR_RESOURCE_DUPLICATE", f"duplicate FHIR resource: {resource_key}"
                )
            seen_resource_keys.add(resource_key)
            expected_filename = f"{resource_type}-{resource_id}.json"
            if PurePosixPath(path).name != expected_filename:
                raise FHIRPackageValidationError(
                    "FHIR_FILENAME_MISMATCH",
                    f"FHIR resource filename does not match its identity: {path}",
                )
            narrative = payload.get("text")
            narrative_div = narrative.get("div") if isinstance(narrative, dict) else None
            profiles = payload.get("meta", {}).get("profile", [])
            if not isinstance(profiles, list) or not all(
                isinstance(profile, str) for profile in profiles
            ):
                raise FHIRPackageValidationError(
                    "FHIR_PROFILE_INVALID", f"FHIR meta.profile is invalid: {path}"
                )
            logical_reference = self._logical_reference(payload, resource_id)
            resources.append(
                FHIRResourceIndexEntry(
                    resource_key=resource_key,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    logical_reference=logical_reference,
                    canonical_url=self._optional_string(payload, "url", path),
                    version=self._optional_string(payload, "version", path),
                    status=self._optional_string(payload, "status", path),
                    experimental=self._optional_bool(payload, "experimental", path),
                    profiles=tuple(profiles),
                    member_path=path,
                    content_sha256=hashlib.sha256(raw).hexdigest(),
                    byte_size=len(raw),
                    narrative_sha256=(
                        hashlib.sha256(narrative_div.encode("utf-8")).hexdigest()
                        if isinstance(narrative_div, str)
                        else None
                    ),
                    declared_in_implementation_guide=(
                        reference in declared or logical_reference in declared
                    ),
                    is_example=is_example,
                )
            )
        resources_tuple = tuple(sorted(resources, key=lambda item: item.resource_key))
        available_references = {
            f"{item.resource_type}/{item.resource_id}" for item in resources_tuple
        }
        available_references.update(
            item.logical_reference
            for item in resources_tuple
            if item.logical_reference is not None
        )
        missing = tuple(sorted(declared - available_references))
        undeclared = tuple(
            sorted(
                f"{item.resource_type}/{item.resource_id}"
                for item in resources_tuple
                if not item.is_example
                and item.resource_type != "ImplementationGuide"
                and not item.declared_in_implementation_guide
            )
        )
        ig = self._implementation_guide(ig_payload, ig_raw, len(declared))
        counts = dict(sorted(Counter(item.resource_type for item in resources_tuple).items()))
        inventory_payload = [item.model_dump(mode="json") for item in resources_tuple]
        return ParsedFHIRPackage(
            manifest=manifest,
            implementation_guide=ig,
            resources=resources_tuple,
            resource_type_counts=counts,
            resource_inventory_sha256=canonical_sha256({"resources": inventory_payload}),
            missing_declared_resources=missing,
            undeclared_resources=undeclared,
            declared_strings=declared_strings,
        )

    def parse_dependency_manifest(self, content: bytes) -> FHIRDependencyPackageManifest:
        members = self._read_json_members(content, manifest_only=True)
        raw, payload = self._required_member(members, "package/package.json")
        required = ("name", "version")
        if any(
            not isinstance(payload.get(key), str) or not payload[key]
            for key in required
        ):
            raise FHIRPackageValidationError(
                "PACKAGE_MANIFEST_INVALID",
                "dependency package manifest is missing its identity",
            )
        dependencies = self._dependencies(payload)
        fhir_versions = payload.get("fhirVersions", [])
        if not isinstance(fhir_versions, list) or not all(
            isinstance(item, str) and item for item in fhir_versions
        ):
            raise FHIRPackageValidationError(
                "PACKAGE_MANIFEST_INVALID",
                "dependency package manifest has invalid FHIR versions",
            )
        optional: dict[str, str | None] = {}
        for key in ("title", "type", "canonical", "license"):
            value = payload.get(key)
            if value is not None and not isinstance(value, str):
                raise FHIRPackageValidationError(
                    "PACKAGE_MANIFEST_INVALID",
                    f"dependency package manifest {key} must be a string",
                )
            optional[key] = value
        return FHIRDependencyPackageManifest(
            package_id=payload["name"],
            version=payload["version"],
            title=optional["title"],
            package_type=optional["type"],
            canonical=optional["canonical"],
            fhir_versions=tuple(fhir_versions),
            license=optional["license"],
            dependencies=dependencies,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _read_json_members(
        self, content: bytes, *, manifest_only: bool = False
    ) -> dict[str, tuple[bytes, dict[str, Any]]]:
        if not content.startswith(b"\x1f\x8b"):
            raise FHIRPackageValidationError(
                "ARCHIVE_NOT_GZIP", "FHIR package is not a gzip archive"
            )
        json_members: dict[str, tuple[bytes, dict[str, Any]]] = {}
        seen_paths: set[str] = set()
        total_size = 0
        entry_count = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r|gz") as archive:
                for member in archive:
                    entry_count += 1
                    if entry_count > self._limits.max_entries:
                        raise FHIRPackageValidationError(
                            "ARCHIVE_ENTRY_LIMIT", "FHIR package exceeds the entry limit"
                        )
                    path = self._validate_member(
                        member, require_package_root=not manifest_only
                    )
                    if path in seen_paths:
                        raise FHIRPackageValidationError(
                            "ARCHIVE_DUPLICATE_PATH", f"duplicate archive member: {path}"
                        )
                    seen_paths.add(path)
                    if member.isdir():
                        continue
                    total_size += member.size
                    if total_size > self._limits.max_total_uncompressed_bytes:
                        raise FHIRPackageValidationError(
                            "ARCHIVE_EXPANSION_LIMIT",
                            "FHIR package exceeds the uncompressed byte limit",
                        )
                    if member.size > self._limits.max_member_bytes and (
                        not manifest_only or path == "package/package.json"
                    ):
                        raise FHIRPackageValidationError(
                            "ARCHIVE_MEMBER_LIMIT", f"archive member is too large: {path}"
                        )
                    if not manifest_only and path.lower().endswith(
                        (".tgz", ".tar", ".tar.gz", ".zip")
                    ):
                        raise FHIRPackageValidationError(
                            "NESTED_ARCHIVE", f"nested archive is forbidden: {path}"
                        )
                    if not path.endswith(".json") or (
                        manifest_only and path != "package/package.json"
                    ):
                        continue
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise FHIRPackageValidationError(
                            "ARCHIVE_MEMBER_UNREADABLE", f"cannot read archive member: {path}"
                        )
                    raw = handle.read(self._limits.max_member_bytes + 1)
                    if len(raw) != member.size:
                        raise FHIRPackageValidationError(
                            "ARCHIVE_MEMBER_SIZE_MISMATCH",
                            f"archive member size does not match its header: {path}",
                        )
                    payload = self._load_json(path, raw)
                    json_members[path] = (raw, payload)
        except FHIRPackageValidationError:
            raise
        except (tarfile.TarError, EOFError, OSError) as error:
            raise FHIRPackageValidationError(
                "ARCHIVE_MALFORMED", "FHIR package archive could not be parsed"
            ) from error

        if content and total_size / len(content) > self._limits.max_compression_ratio:
            raise FHIRPackageValidationError(
                "ARCHIVE_COMPRESSION_RATIO",
                "FHIR package exceeds the permitted compression ratio",
            )
        return json_members

    def _validate_member(
        self, member: tarfile.TarInfo, *, require_package_root: bool = True
    ) -> str:
        path = member.name
        pure = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or pure.is_absolute()
            or ".." in pure.parts
            or not pure.parts
            or (require_package_root and pure.parts[0] != "package")
        ):
            raise FHIRPackageValidationError(
                "ARCHIVE_PATH_INVALID", f"unsafe archive member path: {path!r}"
            )
        if not (member.isfile() or member.isdir()):
            raise FHIRPackageValidationError(
                "ARCHIVE_MEMBER_TYPE", f"archive links and special files are forbidden: {path}"
            )
        if member.isfile() and member.size < 0:
            raise FHIRPackageValidationError(
                "ARCHIVE_MEMBER_SIZE", f"archive member has an invalid size: {path}"
            )
        return str(pure)

    def _load_json(self, path: str, raw: bytes) -> dict[str, Any]:
        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise FHIRPackageValidationError(
                        "JSON_DUPLICATE_KEY", f"duplicate JSON key {key!r}: {path}"
                    )
                result[key] = value
            return result

        def reject_nonfinite(value: str) -> None:
            raise FHIRPackageValidationError(
                "JSON_NONFINITE_NUMBER", f"non-finite JSON number {value!r}: {path}"
            )

        try:
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=reject_duplicates,
                parse_constant=reject_nonfinite,
            )
        except FHIRPackageValidationError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise FHIRPackageValidationError(
                "JSON_INVALID", f"archive member is not strict UTF-8 JSON: {path}"
            ) from error
        if not isinstance(payload, dict):
            raise FHIRPackageValidationError(
                "JSON_ROOT_INVALID", f"JSON archive member must be an object: {path}"
            )
        if self._json_depth(payload) > self._limits.max_json_depth:
            raise FHIRPackageValidationError(
                "JSON_DEPTH_LIMIT", f"JSON archive member exceeds depth limit: {path}"
            )
        return payload

    @staticmethod
    def _required_member(
        members: dict[str, tuple[bytes, dict[str, Any]]], path: str
    ) -> tuple[bytes, dict[str, Any]]:
        try:
            return members[path]
        except KeyError as error:
            raise FHIRPackageValidationError(
                "PACKAGE_MANIFEST_MISSING", f"required archive member is missing: {path}"
            ) from error

    @staticmethod
    def _manifest(payload: dict[str, Any], raw: bytes) -> FHIRPackageManifest:
        required = ("name", "version", "canonical", "title", "type", "license")
        if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
            raise FHIRPackageValidationError(
                "PACKAGE_MANIFEST_INVALID", "package manifest is missing required strings"
            )
        fhir_versions = payload.get("fhirVersions")
        if not isinstance(fhir_versions, list) or not all(
            isinstance(item, str) and item for item in fhir_versions
        ):
            raise FHIRPackageValidationError(
                "PACKAGE_MANIFEST_INVALID", "package manifest has invalid FHIR versions"
            )
        dependencies = FHIRPackageParser._dependencies(payload)
        return FHIRPackageManifest(
            package_id=payload["name"],
            version=payload["version"],
            canonical=payload["canonical"],
            title=payload["title"],
            package_type=payload["type"],
            fhir_versions=tuple(fhir_versions),
            license=payload["license"],
            dependencies=dependencies,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
        )

    @staticmethod
    def _dependencies(payload: dict[str, Any]) -> tuple[FHIRPackageDependency, ...]:
        raw_dependencies = payload.get("dependencies", {})
        if not isinstance(raw_dependencies, dict) or not all(
            isinstance(key, str)
            and key
            and isinstance(value, str)
            and value
            for key, value in raw_dependencies.items()
        ):
            raise FHIRPackageValidationError(
                "PACKAGE_MANIFEST_INVALID", "package manifest dependencies are invalid"
            )
        return tuple(
            FHIRPackageDependency(package_id=key, version=value)
            for key, value in raw_dependencies.items()
        )

    @staticmethod
    def _implementation_guide(
        payload: dict[str, Any], raw: bytes, declared_resource_count: int
    ) -> FHIRImplementationGuideMetadata:
        required = (
            "id",
            "packageId",
            "url",
            "version",
            "name",
            "title",
            "status",
            "publisher",
            "license",
        )
        if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
            raise FHIRPackageValidationError(
                "IMPLEMENTATION_GUIDE_INVALID",
                "ImplementationGuide is missing required strings",
            )
        fhir_versions = payload.get("fhirVersion")
        if not isinstance(fhir_versions, list) or not all(
            isinstance(item, str) and item for item in fhir_versions
        ):
            raise FHIRPackageValidationError(
                "IMPLEMENTATION_GUIDE_INVALID",
                "ImplementationGuide.fhirVersion is invalid",
            )
        experimental = payload.get("experimental", False)
        if not isinstance(experimental, bool):
            raise FHIRPackageValidationError(
                "IMPLEMENTATION_GUIDE_INVALID",
                "ImplementationGuide.experimental must be a boolean",
            )
        profiles = payload.get("meta", {}).get("profile", [])
        if not isinstance(profiles, list) or not all(
            isinstance(profile, str) for profile in profiles
        ):
            raise FHIRPackageValidationError(
                "IMPLEMENTATION_GUIDE_INVALID", "ImplementationGuide profiles are invalid"
            )
        return FHIRImplementationGuideMetadata(
            resource_id=payload["id"],
            package_id=payload["packageId"],
            canonical_url=payload["url"],
            version=payload["version"],
            name=payload["name"],
            title=payload["title"],
            status=payload["status"],
            experimental=experimental,
            publisher=payload["publisher"],
            license=payload["license"],
            fhir_versions=tuple(fhir_versions),
            profiles=tuple(profiles),
            declared_resource_count=declared_resource_count,
            content_sha256=hashlib.sha256(raw).hexdigest(),
        )

    @staticmethod
    def _declared_resources(payload: dict[str, Any]) -> set[str]:
        definition = payload.get("definition", {})
        resources = definition.get("resource", []) if isinstance(definition, dict) else []
        if not isinstance(resources, list):
            raise FHIRPackageValidationError(
                "IMPLEMENTATION_GUIDE_INVALID",
                "ImplementationGuide.definition.resource must be an array",
            )
        declared: set[str] = set()
        for item in resources:
            reference = item.get("reference", {}) if isinstance(item, dict) else {}
            value = reference.get("reference") if isinstance(reference, dict) else None
            if not isinstance(value, str) or "/" not in value:
                raise FHIRPackageValidationError(
                    "IMPLEMENTATION_GUIDE_INVALID",
                    "ImplementationGuide contains an invalid resource reference",
                )
            if value in declared:
                raise FHIRPackageValidationError(
                    "IMPLEMENTATION_GUIDE_DUPLICATE_REFERENCE",
                    f"ImplementationGuide repeats resource reference: {value}",
                )
            declared.add(value)
        return declared

    @staticmethod
    def _optional_string(payload: dict[str, Any], key: str, path: str) -> str | None:
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            raise FHIRPackageValidationError(
                "FHIR_FIELD_TYPE", f"FHIR {key} must be a string: {path}"
            )
        return value

    @staticmethod
    def _optional_bool(payload: dict[str, Any], key: str, path: str) -> bool | None:
        value = payload.get(key)
        if value is not None and not isinstance(value, bool):
            raise FHIRPackageValidationError(
                "FHIR_FIELD_TYPE", f"FHIR {key} must be a boolean: {path}"
            )
        return value

    @staticmethod
    def _logical_reference(payload: dict[str, Any], resource_id: str) -> str | None:
        extensions = payload.get("extension", [])
        if not isinstance(extensions, list):
            return None
        suffix = f"/{resource_id}"
        for extension in extensions:
            if not isinstance(extension, dict):
                continue
            definition_url = extension.get("url")
            canonical = extension.get("valueUri")
            if (
                isinstance(definition_url, str)
                and isinstance(canonical, str)
                and definition_url.endswith(".url")
                and canonical.endswith(suffix)
            ):
                marker = "/extension-"
                if marker in definition_url:
                    logical_type = definition_url.rsplit(marker, 1)[1].removesuffix(
                        ".url"
                    )
                    if logical_type:
                        return f"{logical_type}/{resource_id}"
        return None

    @classmethod
    def _json_depth(cls, value: Any) -> int:
        if isinstance(value, dict):
            return 1 + max((cls._json_depth(item) for item in value.values()), default=0)
        if isinstance(value, list):
            return 1 + max((cls._json_depth(item) for item in value), default=0)
        return 0

    @classmethod
    def _all_strings(cls, value: Any) -> set[str]:
        if isinstance(value, str):
            return {value}
        if isinstance(value, dict):
            result: set[str] = set()
            for item in value.values():
                result.update(cls._all_strings(item))
            return result
        if isinstance(value, list):
            result = set()
            for item in value:
                result.update(cls._all_strings(item))
            return result
        return set()
