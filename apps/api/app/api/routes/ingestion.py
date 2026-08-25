from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_ingestion_service, require_ingestion_auth
from app.ingestion.policy import PublisherPolicyError
from app.ingestion.registry import (
    RegistryConflictError,
    RegistryNotFoundError,
    RegistryStateError,
)
from app.ingestion.service import IngestionService
from app.schemas.ingestion import (
    AcquisitionCreate,
    AcquisitionOutcome,
    PublisherCreate,
    PublisherRecord,
    QuarantineRecord,
    QuarantineResolution,
    SourceCreate,
    SourceRecord,
    SourceVersionCreate,
    SourceVersionRecord,
)

router = APIRouter(
    prefix="/ingestion",
    tags=["ingestion"],
    dependencies=[Depends(require_ingestion_auth)],
)
IngestionServiceDependency = Annotated[IngestionService, Depends(get_ingestion_service)]


def _translate_registry_error(error: Exception) -> HTTPException:
    if isinstance(error, RegistryNotFoundError):
        return HTTPException(status_code=404, detail=str(error.args[0]))
    if isinstance(error, (RegistryConflictError, RegistryStateError)):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, PublisherPolicyError):
        return HTTPException(
            status_code=422,
            detail={"reason_code": error.reason_code, "message": str(error)},
        )
    raise error


@router.post(
    "/publishers", response_model=PublisherRecord, status_code=status.HTTP_201_CREATED
)
async def create_publisher(
    payload: PublisherCreate, service: IngestionServiceDependency
) -> PublisherRecord:
    try:
        return await service.create_publisher(payload)
    except (RegistryConflictError, RegistryNotFoundError) as error:
        raise _translate_registry_error(error) from error


@router.post("/sources", response_model=SourceRecord, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: SourceCreate, service: IngestionServiceDependency
) -> SourceRecord:
    try:
        return await service.create_source(payload)
    except (RegistryConflictError, RegistryNotFoundError, PublisherPolicyError) as error:
        raise _translate_registry_error(error) from error


@router.post(
    "/source-versions",
    response_model=SourceVersionRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_source_version(
    payload: SourceVersionCreate, service: IngestionServiceDependency
) -> SourceVersionRecord:
    try:
        return await service.create_source_version(payload)
    except (RegistryConflictError, RegistryNotFoundError) as error:
        raise _translate_registry_error(error) from error


@router.post(
    "/source-versions/{source_version_id}/acquisitions",
    response_model=AcquisitionOutcome,
)
async def acquire_source_version(
    source_version_id: str,
    payload: AcquisitionCreate,
    service: IngestionServiceDependency,
) -> AcquisitionOutcome:
    try:
        return await service.acquire(source_version_id, payload)
    except (
        RegistryConflictError,
        RegistryNotFoundError,
        RegistryStateError,
        PublisherPolicyError,
    ) as error:
        raise _translate_registry_error(error) from error


@router.get("/quarantine", response_model=list[QuarantineRecord])
async def list_quarantine(service: IngestionServiceDependency) -> list[QuarantineRecord]:
    return await service.list_quarantine()


@router.post(
    "/quarantine/{quarantine_event_id}/resolve", response_model=QuarantineRecord
)
async def resolve_quarantine(
    quarantine_event_id: str,
    payload: QuarantineResolution,
    service: IngestionServiceDependency,
) -> QuarantineRecord:
    try:
        return await service.resolve_quarantine(quarantine_event_id, payload)
    except (RegistryNotFoundError, RegistryStateError) as error:
        raise _translate_registry_error(error) from error
