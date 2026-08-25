from __future__ import annotations

import asyncio
import ipaddress
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from app.corpus_steward.schemas import InventoryItem, TrustRootDefinition
from app.ingestion.policy import PublisherDomainPolicy
from app.schemas.domain import utc_now

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class ConnectorError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ConditionalMetadata:
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class ConnectorRequest:
    url: str
    accept: str
    conditional: ConditionalMetadata | None = None


@dataclass(frozen=True)
class ConnectorResponse:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    fetched_at: datetime

    @property
    def media_type(self) -> str:
        return self.headers.get("content-type", "application/octet-stream").split(";", 1)[
            0
        ].strip().lower()


@dataclass(frozen=True)
class RawInventory:
    cutoff_at: datetime
    items: tuple[InventoryItem, ...]
    responses: tuple[ConnectorResponse, ...]


@dataclass(frozen=True)
class ParsedPage:
    items: tuple[InventoryItem, ...]
    next_cursor: str | None = None


class HTTPConnectorTransport:
    """Bounded HTTPS transport with redirect/domain/SSRF and conditional-fetch policy."""

    def __init__(
        self,
        *,
        max_bytes: int,
        timeout_seconds: float,
        allow_private_networks: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        max_redirects: int = 5,
    ) -> None:
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self._allow_private_networks = allow_private_networks
        self._transport = transport
        self._max_redirects = max_redirects

    async def fetch(
        self,
        request: ConnectorRequest,
        *,
        allowed_domains: tuple[str, ...],
    ) -> ConnectorResponse:
        policy = PublisherDomainPolicy(frozenset(allowed_domains))
        current_url = request.url
        headers = {"Accept": request.accept}
        if request.conditional is not None:
            if request.conditional.etag:
                headers["If-None-Match"] = request.conditional.etag
            if request.conditional.last_modified:
                headers["If-Modified-Since"] = request.conditional.last_modified

        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            for redirect_count in range(self._max_redirects + 1):
                hostname = policy.validate_url(current_url)
                await self._reject_private_destination(hostname)
                try:
                    async with client.stream("GET", current_url, headers=headers) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if location is None:
                                raise ConnectorError(
                                    "REDIRECT_WITHOUT_LOCATION",
                                    "publisher returned a redirect without a location",
                                )
                            if redirect_count == self._max_redirects:
                                raise ConnectorError(
                                    "TOO_MANY_REDIRECTS", "publisher redirect limit exceeded"
                                )
                            current_url = urljoin(str(response.url), location)
                            continue
                        if response.status_code == 304:
                            return self._response(request.url, response, b"")
                        if not 200 <= response.status_code < 300:
                            raise ConnectorError(
                                "PUBLISHER_HTTP_ERROR",
                                f"publisher returned HTTP {response.status_code}",
                            )
                        declared = response.headers.get("content-length")
                        if declared is not None:
                            try:
                                if int(declared) > self._max_bytes:
                                    raise ConnectorError(
                                        "ARTIFACT_TOO_LARGE",
                                        "publisher artifact exceeds the configured limit",
                                    )
                            except ValueError as error:
                                raise ConnectorError(
                                    "INVALID_CONTENT_LENGTH",
                                    "publisher returned an invalid content length",
                                ) from error
                        chunks: list[bytes] = []
                        byte_count = 0
                        async for chunk in response.aiter_bytes():
                            byte_count += len(chunk)
                            if byte_count > self._max_bytes:
                                raise ConnectorError(
                                    "ARTIFACT_TOO_LARGE",
                                    "publisher artifact exceeds the configured limit",
                                )
                            chunks.append(chunk)
                        return self._response(request.url, response, b"".join(chunks))
                except ConnectorError:
                    raise
                except httpx.HTTPError as error:
                    raise ConnectorError(
                        "PUBLISHER_CONNECTION_FAILED", "publisher request failed"
                    ) from error
        raise ConnectorError("TOO_MANY_REDIRECTS", "publisher redirect limit exceeded")

    @staticmethod
    def _response(
        requested_url: str, response: httpx.Response, body: bytes
    ) -> ConnectorResponse:
        return ConnectorResponse(
            requested_url=requested_url,
            final_url=str(response.url),
            status_code=response.status_code,
            headers={key.lower(): value for key, value in response.headers.items()},
            body=body,
            fetched_at=utc_now(),
        )

    async def _reject_private_destination(self, hostname: str) -> None:
        if self._allow_private_networks:
            return
        try:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo, hostname, 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror as error:
            raise ConnectorError(
                "PUBLISHER_DNS_FAILED", "publisher hostname could not be resolved"
            ) from error
        if not addresses:
            raise ConnectorError(
                "PUBLISHER_DNS_FAILED", "publisher hostname did not resolve"
            )
        for address in addresses:
            if not ipaddress.ip_address(address[4][0]).is_global:
                raise ConnectorError(
                    "PRIVATE_NETWORK_DESTINATION",
                    "publisher hostname resolves to a non-public network",
                )


class Connector(ABC):
    name: str
    version: str

    @abstractmethod
    async def enumerate_inventory(
        self,
        trust_root: TrustRootDefinition,
        transport: HTTPConnectorTransport,
    ) -> RawInventory:
        raise NotImplementedError

    async def fetch_artifact(
        self,
        trust_root: TrustRootDefinition,
        item: InventoryItem,
        transport: HTTPConnectorTransport,
        conditional: ConditionalMetadata | None,
    ) -> ConnectorResponse:
        return await transport.fetch(
            ConnectorRequest(
                url=item.artifact_url,
                accept=item.media_type,
                conditional=conditional,
            ),
            allowed_domains=trust_root.allowed_domains,
        )


class PaginatedHTTPConnector(Connector, ABC):
    max_pages = 1000

    async def enumerate_inventory(
        self,
        trust_root: TrustRootDefinition,
        transport: HTTPConnectorTransport,
    ) -> RawInventory:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        items: list[InventoryItem] = []
        responses: list[ConnectorResponse] = []
        cutoff_at = utc_now()
        for _ in range(self.max_pages):
            request = self.inventory_request(trust_root, cursor)
            response = await transport.fetch(
                request, allowed_domains=trust_root.allowed_domains
            )
            responses.append(response)
            parsed = self.parse_inventory_page(trust_root, response)
            items.extend(parsed.items)
            if parsed.next_cursor is None:
                break
            if parsed.next_cursor in seen_cursors:
                raise ConnectorError("PAGINATION_LOOP", "inventory pagination cursor repeated")
            seen_cursors.add(parsed.next_cursor)
            cursor = parsed.next_cursor
        else:
            raise ConnectorError("PAGINATION_LIMIT", "inventory exceeded the page limit")
        ids = [item.item_id for item in items]
        if len(ids) != len(set(ids)):
            raise ConnectorError(
                "DUPLICATE_INVENTORY_ITEM", "publisher inventory repeated an item ID"
            )
        return RawInventory(
            cutoff_at=cutoff_at,
            items=tuple(sorted(items, key=lambda item: item.item_id)),
            responses=tuple(responses),
        )

    @abstractmethod
    def inventory_request(
        self, trust_root: TrustRootDefinition, cursor: str | None
    ) -> ConnectorRequest:
        raise NotImplementedError

    @abstractmethod
    def parse_inventory_page(
        self, trust_root: TrustRootDefinition, response: ConnectorResponse
    ) -> ParsedPage:
        raise NotImplementedError


def require_string(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectorError("CONNECTOR_CONFIG_INVALID", f"{key} must be a non-empty string")
    return value
