from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

from app.corpus_steward.connectors.base import (
    ConditionalMetadata,
    ConnectorError,
    ConnectorRequest,
    ConnectorResponse,
    HTTPConnectorTransport,
    PaginatedHTTPConnector,
    ParsedPage,
    require_string,
)
from app.corpus_steward.schemas import InventoryItem, TrustRootDefinition
from app.schemas.domain import SourceStatus


class WHOSmartFHIRConnector(PaginatedHTTPConnector):
    """Official FHIR publication connector for one configured WHO SMART guide."""

    name = "who-smart-fhir"
    version = "1.0.0"

    async def fetch_artifact(
        self,
        trust_root: TrustRootDefinition,
        item: InventoryItem,
        transport: HTTPConnectorTransport,
        conditional: ConditionalMetadata | None,
    ) -> ConnectorResponse:
        response = await super().fetch_artifact(
            trust_root, item, transport, conditional
        )
        if response.status_code != 304 and not response.body.startswith(b"\x1f\x8b"):
            raise ConnectorError(
                "FHIR_PACKAGE_INVALID",
                "WHO FHIR package does not have a gzip archive signature",
            )
        return response

    def inventory_request(
        self, trust_root: TrustRootDefinition, cursor: str | None
    ) -> ConnectorRequest:
        if cursor is not None:
            raise ConnectorError(
                "UNEXPECTED_PAGINATION", "WHO FHIR package lists are single-page inventories"
            )
        return ConnectorRequest(
            url=require_string(trust_root.connector_config, "inventory_url"),
            accept="application/json",
        )

    def parse_inventory_page(
        self, trust_root: TrustRootDefinition, response: ConnectorResponse
    ) -> ParsedPage:
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConnectorError(
                "INVENTORY_INVALID_JSON", "WHO package inventory is not valid JSON"
            ) from error
        if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
            raise ConnectorError(
                "INVENTORY_SCHEMA_INVALID", "WHO package inventory has no list array"
            )

        config = trust_root.connector_config
        package_id = require_string(config, "package_id")
        guide_url = require_string(config, "guide_url").rstrip("/") + "/"
        artifact_url = str(config.get("artifact_url") or urljoin(guide_url, "package.tgz"))
        current_releases_only = config.get("current_releases_only", True)
        entries: list[InventoryItem] = []
        for raw_entry in payload["list"]:
            if not isinstance(raw_entry, dict):
                raise ConnectorError(
                    "INVENTORY_SCHEMA_INVALID", "WHO package-list entries must be objects"
                )
            version = raw_entry.get("version")
            status = raw_entry.get("status")
            if not isinstance(version, str) or not isinstance(status, str):
                raise ConnectorError(
                    "INVENTORY_SCHEMA_INVALID",
                    "WHO package-list entries require version and status",
                )
            if status != "release":
                continue
            is_current = raw_entry.get("current") is True
            if current_releases_only and not is_current:
                continue
            path = raw_entry.get("path")
            canonical_url = self._https_url(path) if isinstance(path, str) else guide_url
            item_artifact_url = (
                artifact_url
                if is_current
                else urljoin(canonical_url + "/", "package.tgz")
            )
            entries.append(
                InventoryItem(
                    item_id=f"{package_id}#{version}",
                    title=str(config.get("title") or package_id),
                    version=version,
                    lifecycle_status=(
                        SourceStatus.EFFECTIVE if is_current else SourceStatus.SUPERSEDED
                    ),
                    canonical_url=canonical_url,
                    artifact_url=item_artifact_url,
                    media_type="application/gzip",
                    publisher_metadata=self._publisher_metadata(raw_entry),
                )
            )
        if not entries:
            raise ConnectorError(
                "EMPTY_AUTHORITATIVE_INVENTORY",
                "WHO package list contained no releases inside the configured scope",
            )
        return ParsedPage(items=tuple(entries))

    @staticmethod
    def _https_url(value: str) -> str:
        if value.startswith("http://smart.who.int/"):
            return "https://" + value.removeprefix("http://")
        return value.rstrip("/")

    @staticmethod
    def _publisher_metadata(entry: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "version",
            "status",
            "sequence",
            "fhirversion",
            "date",
            "current",
            "desc",
            "path",
        }
        return {key: entry[key] for key in sorted(entry) if key in allowed}
