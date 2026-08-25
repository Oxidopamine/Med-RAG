import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from app.ingestion.policy import PublisherDomainPolicy

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_PDF_MEDIA_TYPES = {"application/pdf", "application/octet-stream", "binary/octet-stream"}


class AcquisitionDownloadError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class DownloadedPDF:
    content: bytes
    requested_url: str
    final_url: str
    publisher_domain: str
    etag: str | None
    last_modified: str | None


class AsyncPDFDownloader:
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
        self._timeout = timeout_seconds
        self._allow_private_networks = allow_private_networks
        self._transport = transport
        self._max_redirects = max_redirects

    async def download(self, url: str, policy: PublisherDomainPolicy) -> DownloadedPDF:
        requested_url = url
        current_url = url
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            for redirect_count in range(self._max_redirects + 1):
                domain = policy.validate_url(current_url)
                await self._reject_private_destination(domain)
                try:
                    async with client.stream(
                        "GET",
                        current_url,
                        headers={"Accept": "application/pdf,application/octet-stream;q=0.8"},
                    ) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if location is None:
                                raise AcquisitionDownloadError(
                                    "REDIRECT_WITHOUT_LOCATION",
                                    "publisher returned a redirect without a location",
                                )
                            if redirect_count == self._max_redirects:
                                raise AcquisitionDownloadError(
                                    "TOO_MANY_REDIRECTS", "publisher redirect limit exceeded"
                                )
                            current_url = urljoin(str(response.url), location)
                            continue
                        try:
                            response.raise_for_status()
                        except httpx.HTTPStatusError as error:
                            raise AcquisitionDownloadError(
                                "PUBLISHER_HTTP_ERROR",
                                f"publisher returned HTTP {response.status_code}",
                            ) from error

                        content_type = response.headers.get("content-type", "")
                        media_type = content_type.split(";", 1)[0].strip().lower()
                        if media_type and media_type not in _PDF_MEDIA_TYPES:
                            raise AcquisitionDownloadError(
                                "UNEXPECTED_MEDIA_TYPE",
                                "publisher response is not identified as a PDF",
                            )
                        content_length = response.headers.get("content-length")
                        if content_length is not None:
                            try:
                                declared_size = int(content_length)
                            except ValueError as error:
                                raise AcquisitionDownloadError(
                                    "INVALID_CONTENT_LENGTH",
                                    "publisher returned an invalid content length",
                                ) from error
                            if declared_size > self._max_bytes:
                                raise AcquisitionDownloadError(
                                    "PDF_TOO_LARGE", "publisher PDF exceeds the configured limit"
                                )

                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > self._max_bytes:
                                raise AcquisitionDownloadError(
                                    "PDF_TOO_LARGE", "publisher PDF exceeds the configured limit"
                                )
                            chunks.append(chunk)
                        return DownloadedPDF(
                            content=b"".join(chunks),
                            requested_url=requested_url,
                            final_url=str(response.url),
                            publisher_domain=policy.validate_url(str(response.url)),
                            etag=response.headers.get("etag"),
                            last_modified=response.headers.get("last-modified"),
                        )
                except httpx.HTTPError as error:
                    raise AcquisitionDownloadError(
                        "PUBLISHER_CONNECTION_FAILED", "could not acquire the publisher PDF"
                    ) from error
        raise AcquisitionDownloadError("TOO_MANY_REDIRECTS", "publisher redirect limit exceeded")

    async def _reject_private_destination(self, hostname: str) -> None:
        if self._allow_private_networks:
            return
        try:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo, hostname, 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror as error:
            raise AcquisitionDownloadError(
                "PUBLISHER_DNS_FAILED", "publisher hostname could not be resolved"
            ) from error
        if not addresses:
            raise AcquisitionDownloadError(
                "PUBLISHER_DNS_FAILED", "publisher hostname did not resolve"
            )
        for address in addresses:
            value = ipaddress.ip_address(address[4][0])
            if not value.is_global:
                raise AcquisitionDownloadError(
                    "PRIVATE_NETWORK_DESTINATION",
                    "publisher hostname resolves to a non-public network",
                )
