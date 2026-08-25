"""Persistence for immutable structured narrative and dependency closures."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.structured_input_schemas import (
    ResolvedDependencyPackage,
    StructuredInputClosureReport,
    StructuredInputState,
)
from app.persistence.database import Database
from app.persistence.models import (
    StructuredDependencyPackageRow,
    StructuredInputRunRow,
    StructuredNarrativeArtifactRow,
)


class StructuredInputRepositoryError(RuntimeError):
    pass


class StructuredInputRepositoryConflictError(StructuredInputRepositoryError):
    pass


@dataclass(frozen=True)
class StoredStructuredInputRun:
    state: StructuredInputState
    report: StructuredInputClosureReport
    report_artifact_sha256: str
    attestation_id: str


class SQLStructuredInputRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def existing(
        self,
        *,
        candidate_id: str,
        item_id: str,
        resolver_name: str,
        resolver_version: str,
    ) -> StoredStructuredInputRun | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(StructuredInputRunRow).where(
                    StructuredInputRunRow.reconciliation_candidate_id == candidate_id,
                    StructuredInputRunRow.inventory_item_id == item_id,
                    StructuredInputRunRow.resolver_name == resolver_name,
                    StructuredInputRunRow.resolver_version == resolver_version,
                )
            )
            if row is None:
                return None
            report = StructuredInputClosureReport.model_validate(row.report)
            if (
                report.report_sha256 != row.report_sha256
                or report.content.input_run_id != row.input_run_id
                or list(report.content.blockers) != row.blockers
                or len(report.content.dependency_packages) != row.dependency_count
                or len(report.content.narrative_artifacts)
                != row.narrative_artifact_count
            ):
                raise StructuredInputRepositoryError(
                    "stored structured input closure is inconsistent"
                )
            return StoredStructuredInputRun(
                state=StructuredInputState(row.state),
                report=report,
                report_artifact_sha256=row.report_artifact_sha256,
                attestation_id=row.attestation_id,
            )

    async def record(
        self,
        *,
        state: StructuredInputState,
        report: StructuredInputClosureReport,
        report_artifact_sha256: str,
        attestation_id: str,
    ) -> None:
        content = report.content
        expected_state = (
            StructuredInputState.RESOLVED
            if content.complete
            else StructuredInputState.BLOCKED
        )
        if state is not expected_state:
            raise StructuredInputRepositoryError(
                "structured input state does not match report completeness"
            )
        requirement_rows: dict[tuple[str, str], list] = {}
        for requirement in content.requirements:
            requirement_rows.setdefault(
                (requirement.package_id, requirement.resolved_version), []
            ).append(requirement)
        try:
            async with self._database.session() as session:
                existing = await session.get(StructuredInputRunRow, content.input_run_id)
                if existing is not None:
                    if existing.report_sha256 != report.report_sha256:
                        raise StructuredInputRepositoryConflictError(
                            "structured input run ID identifies a different report"
                        )
                    return
                session.add(
                    StructuredInputRunRow(
                        input_run_id=content.input_run_id,
                        reconciliation_candidate_id=content.reconciliation_candidate_id,
                        trust_root_id=content.trust_root_id,
                        trust_root_sha256=content.trust_root_sha256,
                        inventory_item_id=content.inventory_item_id,
                        source_artifact_sha256=content.source_artifact_sha256,
                        resolver_name=content.resolver_name,
                        resolver_version=content.resolver_version,
                        state=state.value,
                        report_sha256=report.report_sha256,
                        report=report.model_dump(mode="json"),
                        report_artifact_sha256=report_artifact_sha256,
                        attestation_id=attestation_id,
                        dependency_count=len(content.dependency_packages),
                        narrative_artifact_count=len(content.narrative_artifacts),
                        blockers=list(content.blockers),
                        completed_at=content.completed_at,
                    )
                )
                await session.flush()
                for package in content.dependency_packages:
                    requirements = requirement_rows.get(
                        (package.package_id, package.version), []
                    )
                    if not requirements:
                        raise StructuredInputRepositoryError(
                            "resolved dependency has no graph requirement"
                        )
                    session.add(
                        self._dependency_row(
                            content.input_run_id, package, requirements
                        )
                    )
                for artifact in content.narrative_artifacts:
                    session.add(
                        StructuredNarrativeArtifactRow(
                            input_run_id=content.input_run_id,
                            link_id=artifact.link_id,
                            asset_id=artifact.asset_id,
                            title=artifact.title,
                            role=artifact.role.value,
                            configured_url=artifact.configured_url,
                            final_url=artifact.final_url,
                            media_type=artifact.media_type,
                            artifact_sha256=artifact.artifact_sha256,
                            byte_size=artifact.byte_size,
                            fetched_at=artifact.fetched_at,
                            etag=artifact.etag,
                            last_modified=artifact.last_modified,
                        )
                    )
        except IntegrityError as error:
            raise StructuredInputRepositoryConflictError(
                f"structured input persistence conflict: {error.orig}"
            ) from error

    @staticmethod
    def _dependency_row(
        input_run_id: str,
        package: ResolvedDependencyPackage,
        requirements: list,
    ) -> StructuredDependencyPackageRow:
        return StructuredDependencyPackageRow(
            input_run_id=input_run_id,
            package_id=package.package_id,
            version=package.version,
            direct=any(item.direct for item in requirements),
            minimum_depth=min(item.depth for item in requirements),
            registry_metadata_artifact_sha256=(
                package.registry_metadata_artifact_sha256
            ),
            package_artifact_sha256=package.package_artifact_sha256,
            byte_size=package.byte_size,
            registry_url=package.registry_url,
            tarball_url=package.tarball_url,
            registry_sha1=package.registry_sha1,
            manifest_sha256=package.manifest.manifest_sha256,
            manifest=package.manifest.model_dump(mode="json"),
            fetched_at=package.fetched_at,
            etag=package.etag,
            last_modified=package.last_modified,
        )
