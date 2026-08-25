from __future__ import annotations

import base64
import binascii
import hashlib
import json

from app.corpus_steward.connectors.base import (
    ConditionalMetadata,
    Connector,
    ConnectorError,
    ConnectorResponse,
    HTTPConnectorTransport,
    RawInventory,
)
from app.corpus_steward.schemas import InventoryItem, TrustRootDefinition
from app.schemas.domain import SourceStatus, utc_now


class SyntheticConnector(Connector):
    """Deterministic connector for pagination, drift, conditional, and failure tests."""

    name = "synthetic"
    version = "1.0.0"

    async def enumerate_inventory(
        self,
        trust_root: TrustRootDefinition,
        transport: HTTPConnectorTransport,
    ) -> RawInventory:
        del transport
        config = trust_root.connector_config
        raw_items = config.get("items", [])
        if not isinstance(raw_items, list):
            raise ConnectorError("CONNECTOR_CONFIG_INVALID", "synthetic items must be a list")
        page_size = config.get("page_size", max(1, len(raw_items)))
        if not isinstance(page_size, int) or page_size < 1:
            raise ConnectorError(
                "CONNECTOR_CONFIG_INVALID", "synthetic page_size must be positive"
            )
        fail_page = config.get("fail_inventory_page")
        now = utc_now()
        items: list[InventoryItem] = []
        responses: list[ConnectorResponse] = []
        for page_number, offset in enumerate(range(0, len(raw_items), page_size), start=1):
            if fail_page == page_number:
                raise ConnectorError("SYNTHETIC_INVENTORY_FAILURE", "injected page failure")
            page = raw_items[offset : offset + page_size]
            body = json.dumps(
                {"items": page, "page": page_number},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            url = f"https://fixtures.invalid/inventory?page={page_number}"
            responses.append(
                ConnectorResponse(
                    requested_url=url,
                    final_url=url,
                    status_code=200,
                    headers={"content-type": "application/json", "etag": self._etag(body)},
                    body=body,
                    fetched_at=now,
                )
            )
            items.extend(self._item(raw) for raw in page)
        if not responses:
            body = b'{"items":[],"page":1}'
            responses.append(
                ConnectorResponse(
                    requested_url="https://fixtures.invalid/inventory?page=1",
                    final_url="https://fixtures.invalid/inventory?page=1",
                    status_code=200,
                    headers={"content-type": "application/json", "etag": self._etag(body)},
                    body=body,
                    fetched_at=now,
                )
            )
        return RawInventory(cutoff_at=now, items=tuple(items), responses=tuple(responses))

    async def fetch_artifact(
        self,
        trust_root: TrustRootDefinition,
        item: InventoryItem,
        transport: HTTPConnectorTransport,
        conditional: ConditionalMetadata | None,
    ) -> ConnectorResponse:
        del transport
        raw = next(
            entry
            for entry in trust_root.connector_config.get("items", [])
            if entry.get("item_id") == item.item_id
        )
        if item.item_id in trust_root.connector_config.get("fail_artifact_item_ids", []):
            raise ConnectorError("SYNTHETIC_ARTIFACT_FAILURE", "injected artifact failure")
        content = self._content(raw, default=item.item_id)
        etag = self._etag(content)
        status = 304 if conditional is not None and conditional.etag == etag else 200
        return ConnectorResponse(
            requested_url=item.artifact_url,
            final_url=item.artifact_url,
            status_code=status,
            headers={"content-type": item.media_type, "etag": etag},
            body=b"" if status == 304 else content,
            fetched_at=utc_now(),
        )

    @classmethod
    def _item(cls, raw: object) -> InventoryItem:
        if not isinstance(raw, dict):
            raise ConnectorError("CONNECTOR_CONFIG_INVALID", "synthetic items must be objects")
        try:
            content = cls._content(raw, default=str(raw["item_id"]))
            return InventoryItem(
                item_id=str(raw["item_id"]),
                title=str(raw.get("title", raw["item_id"])),
                version=str(raw.get("version", "1")),
                lifecycle_status=SourceStatus(str(raw.get("lifecycle_status", "EFFECTIVE"))),
                canonical_url=str(
                    raw.get("canonical_url", f"https://fixtures.invalid/{raw['item_id']}")
                ),
                artifact_url=str(
                    raw.get("artifact_url", f"https://fixtures.invalid/{raw['item_id']}.bin")
                ),
                media_type=str(raw.get("media_type", "application/octet-stream")),
                publisher_metadata=(
                    raw["publisher_metadata"]
                    if isinstance(raw.get("publisher_metadata"), dict)
                    else {"content_sha256": hashlib.sha256(content).hexdigest()}
                ),
            )
        except (KeyError, ValueError) as error:
            raise ConnectorError(
                "CONNECTOR_CONFIG_INVALID", "synthetic inventory item is invalid"
            ) from error

    @staticmethod
    def _content(raw: dict[str, object], *, default: str) -> bytes:
        encoded = raw.get("content_base64")
        if encoded is None:
            return str(raw.get("content", default)).encode("utf-8")
        if not isinstance(encoded, str):
            raise ConnectorError(
                "CONNECTOR_CONFIG_INVALID", "synthetic content_base64 must be a string"
            )
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ConnectorError(
                "CONNECTOR_CONFIG_INVALID", "synthetic content_base64 is invalid"
            ) from error

    @staticmethod
    def _etag(content: bytes) -> str:
        return f'"{hashlib.sha256(content).hexdigest()}"'
