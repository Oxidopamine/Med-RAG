from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from app.corpus_steward.connectors.base import (
    ConnectorError,
    ConnectorRequest,
    ConnectorResponse,
    HTTPConnectorTransport,
    PaginatedHTTPConnector,
    ParsedPage,
    RawInventory,
    require_string,
)
from app.corpus_steward.schemas import InventoryItem, TrustRootDefinition
from app.schemas.domain import SourceStatus

_UNRESOLVED_ARTIFACT = "urn:who-guidelines-hub:unresolved"


class WHOGuidelinesHubConnector(PaginatedHTTPConnector):
    """Official WHO guidelines inventory from the publications OData hub.

    WHO curates the population itself: publications attributed to the guidelines
    publishing office are exactly the catalogue WHO renders as "WHO guidelines",
    vetted by the Guidelines Review Committee. The connector does not decide what
    counts as a guideline.

    Roughly a third of catalogue records carry no ``DownloadUrl``, and the gap is
    concentrated in the most recent publications. Those are resolved through the
    IRIS REST API using the record's own ``IRISID`` handle, so currency does not
    depend on which of the two WHO systems happens to be populated.
    """

    name = "who-guidelines-hub"
    version = "1.0.0"

    max_pages = 200

    async def enumerate_inventory(
        self,
        trust_root: TrustRootDefinition,
        transport: HTTPConnectorTransport,
    ) -> RawInventory:
        inventory = await super().enumerate_inventory(trust_root, transport)
        config = trust_root.connector_config
        if not config.get("resolve_missing_artifacts", True):
            unresolved = [
                item.item_id
                for item in inventory.items
                if item.artifact_url == _UNRESOLVED_ARTIFACT
            ]
            if unresolved:
                raise ConnectorError(
                    "ARTIFACT_URL_MISSING",
                    f"{len(unresolved)} catalogue records have no download URL",
                )
            return inventory

        resolved: list[InventoryItem] = []
        responses = list(inventory.responses)
        for item in inventory.items:
            if item.artifact_url != _UNRESOLVED_ARTIFACT:
                resolved.append(item)
                continue
            if item.publisher_metadata.get("acquisition_blocker"):
                resolved.append(item)
                continue
            item, extra = await self._resolve_via_iris(trust_root, item, transport)
            responses.extend(extra)
            resolved.append(item)
        return RawInventory(
            cutoff_at=inventory.cutoff_at,
            items=tuple(sorted(resolved, key=lambda entry: entry.item_id)),
            responses=tuple(responses),
        )

    def inventory_request(
        self, trust_root: TrustRootDefinition, cursor: str | None
    ) -> ConnectorRequest:
        config = trust_root.connector_config
        hub_url = require_string(config, "hub_url")
        office_id = require_string(config, "publishing_office_id")
        page_size = int(config.get("page_size", 100))
        if not 1 <= page_size <= 200:
            raise ConnectorError(
                "CONNECTOR_CONFIG_INVALID", "page_size must be between 1 and 200"
            )
        try:
            skip = int(cursor) if cursor is not None else 0
        except ValueError as error:
            raise ConnectorError(
                "PAGINATION_CURSOR_INVALID", "inventory cursor must be an integer offset"
            ) from error

        select = ",".join(
            (
                "Id",
                "Title",
                "Subtitle",
                "ISBN",
                "IRISID",
                "DownloadUrl",
                "ItemDefaultUrl",
                "PublicationDateAndTime",
                "LastModified",
                "WHOReferenceNumber",
                "Copyright",
            )
        )
        # Order by Id: publication dates are neither unique nor stable enough to
        # page against, and a shifting sort silently drops or repeats records.
        query = (
            f"$filter=publishingoffices/any(o:o eq {office_id})"
            f"&$select={select}"
            f"&$orderby=Id"
            f"&$top={page_size}&$skip={skip}&$count=true"
        )
        return ConnectorRequest(
            url=f"{hub_url}?{quote(query, safe='$=&,()/:')}",
            accept="application/json",
        )

    def parse_inventory_page(
        self, trust_root: TrustRootDefinition, response: ConnectorResponse
    ) -> ParsedPage:
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConnectorError(
                "INVENTORY_INVALID_JSON", "WHO publications hub returned invalid JSON"
            ) from error
        if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
            raise ConnectorError(
                "INVENTORY_SCHEMA_INVALID", "WHO publications hub has no value array"
            )

        total = payload.get("@odata.count")
        if not isinstance(total, int) or total < 0:
            raise ConnectorError(
                "INVENTORY_COUNT_MISSING",
                "WHO publications hub did not return @odata.count",
            )

        config = trust_root.connector_config
        skip = self._skip_of(response.requested_url)
        scope_ids = config.get("scope_item_ids")
        if scope_ids is not None and not isinstance(scope_ids, list):
            raise ConnectorError(
                "CONNECTOR_CONFIG_INVALID", "scope_item_ids must be a list when present"
            )
        scope = {str(value).lower() for value in scope_ids} if scope_ids else None

        items: list[InventoryItem] = []
        for record in payload["value"]:
            if not isinstance(record, dict):
                raise ConnectorError(
                    "INVENTORY_SCHEMA_INVALID", "WHO publication entries must be objects"
                )
            item = self._to_item(record, total)
            if scope is not None and item.item_id.lower() not in scope:
                continue
            items.append(item)

        consumed = skip + len(payload["value"])
        next_cursor = str(consumed) if consumed < total and payload["value"] else None
        return ParsedPage(items=tuple(items), next_cursor=next_cursor)

    def _to_item(self, record: dict[str, Any], catalogue_total: int) -> InventoryItem:
        item_id = record.get("Id")
        title = (record.get("Title") or "").strip()
        if not isinstance(item_id, str) or not item_id or not title:
            raise ConnectorError(
                "INVENTORY_SCHEMA_INVALID",
                "WHO publication entries require Id and Title",
            )

        issued = (record.get("PublicationDateAndTime") or "").strip()
        if not issued:
            raise ConnectorError(
                "INVENTORY_SCHEMA_INVALID",
                f"WHO publication {item_id} has no publication date",
            )

        handle = (record.get("IRISID") or "").strip()
        download_url = (record.get("DownloadUrl") or "").strip()
        default_url = (record.get("ItemDefaultUrl") or "").strip()
        canonical_url = (
            f"https://iris.who.int/handle/{handle}"
            if handle
            else f"https://www.who.int/publications/i/item/{default_url}"
        )
        # WHO does not version these records; each edition is its own publication,
        # so the issue date is the only version identity the publisher provides.
        version = issued[:10]

        metadata: dict[str, Any] = {
            "catalogue_total": catalogue_total,
            "copyright": (record.get("Copyright") or "").strip(),
            "hub_download_url": download_url,
            "irisid": handle,
            "isbn": (record.get("ISBN") or "").strip(),
            "item_default_url": default_url,
            "last_modified": (record.get("LastModified") or "").strip(),
            "published_at": issued,
            "reference_number": (record.get("WHOReferenceNumber") or "").strip(),
            "subtitle": (record.get("Subtitle") or "").strip(),
        }
        # ``DownloadUrl`` population is not stable: the same record, same $select,
        # returns the field under one $orderby and null under another (measured
        # 2026-08-27, 198 vs 256 of 358 populated). Binding artifact_url to it
        # would make the inventory fingerprint depend on query shape and report
        # phantom drift on every re-reconciliation. IRIS handle resolution is the
        # deterministic path and also yields a publisher checksum, so the hub URL
        # is retained only as a cross-check.
        artifact_url = _UNRESOLVED_ARTIFACT if handle else download_url
        # A record the publisher lists but does not make retrievable is an
        # exception-register entry, not a connector failure. It stays in the
        # inventory carrying its blocker so the complete-inventory accounting
        # still sees it.
        if not artifact_url:
            artifact_url = _UNRESOLVED_ARTIFACT
            metadata["acquisition_blocker"] = "NO_DOWNLOAD_URL_OR_IRIS_HANDLE"

        return InventoryItem(
            item_id=item_id,
            title=title[:1000],
            version=version,
            # The hub lists currently published guidelines. Supersession between
            # editions is a downstream lifecycle decision, not a publisher field,
            # so nothing here may assert SUPERSEDED.
            lifecycle_status=SourceStatus.EFFECTIVE,
            canonical_url=canonical_url,
            artifact_url=artifact_url,
            media_type="application/pdf",
            publisher_metadata=metadata,
        )

    async def _resolve_via_iris(
        self,
        trust_root: TrustRootDefinition,
        item: InventoryItem,
        transport: HTTPConnectorTransport,
    ) -> tuple[InventoryItem, list[ConnectorResponse]]:
        """Resolve a catalogue record to its IRIS bitstream, preserving every response."""
        config = trust_root.connector_config
        api_base = str(config.get("iris_api_base", "https://iris.who.int/server/api"))
        handle = item.publisher_metadata.get("irisid")
        if not handle:
            raise ConnectorError(
                "ARTIFACT_UNRESOLVABLE",
                f"WHO publication {item.item_id} has no IRIS handle to resolve",
            )

        responses: list[ConnectorResponse] = []

        async def fetch_json(url: str) -> Any:
            response = await transport.fetch(
                ConnectorRequest(url=url, accept="application/json"),
                allowed_domains=trust_root.allowed_domains,
            )
            responses.append(response)
            try:
                return json.loads(response.body)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ConnectorError(
                    "IRIS_INVALID_JSON", f"IRIS returned invalid JSON for {handle}"
                ) from error

        def blocked(reason: str) -> tuple[InventoryItem, list[ConnectorResponse]]:
            """Structural blockers are recorded, not raised.

            Transport and JSON failures still raise: those are retryable faults,
            and turning one into a permanent exception would silently drop a
            record the publisher does list.
            """
            metadata = dict(item.publisher_metadata)
            metadata["acquisition_blocker"] = reason
            return item.model_copy(update={"publisher_metadata": metadata}), responses

        record = await fetch_json(f"{api_base}/pid/find?id=hdl:{handle}")
        uuid = record.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            return blocked("IRIS_ITEM_NOT_FOUND")
        if record.get("withdrawn") is True:
            return blocked("IRIS_ITEM_WITHDRAWN")

        bundles = await fetch_json(f"{api_base}/core/items/{uuid}/bundles")
        originals = [
            bundle
            for bundle in bundles.get("_embedded", {}).get("bundles", [])
            if bundle.get("name") == "ORIGINAL"
        ]
        if not originals:
            return blocked("IRIS_NO_ORIGINAL_BUNDLE")

        candidates: list[dict[str, Any]] = []
        for bundle in originals:
            listing = await fetch_json(
                f"{api_base}/core/bundles/{bundle['uuid']}/bitstreams"
            )
            candidates.extend(listing.get("_embedded", {}).get("bitstreams", []))

        chosen = self._select_bitstream(candidates, config)
        if chosen is None:
            return blocked("IRIS_NO_MATCHING_BITSTREAM")

        checksum = chosen.get("checkSum") or {}
        metadata = dict(item.publisher_metadata)
        resolved_url = f"{api_base}/core/bitstreams/{chosen['uuid']}/content"
        hub_url = metadata.get("hub_download_url") or ""
        if hub_url and hub_url != resolved_url:
            # Not fatal: the hub and IRIS can legitimately expose different
            # renditions. Recorded so a divergence is visible downstream rather
            # than silently resolved in IRIS's favour.
            metadata["hub_download_url_mismatch"] = True
        metadata.update(
            {
                "iris_bitstream_name": chosen.get("name") or "",
                "iris_bitstream_checksum": checksum.get("value") or "",
                "iris_bitstream_checksum_algorithm": checksum.get("checkSumAlgorithm")
                or "",
                "iris_bitstream_size_bytes": chosen.get("sizeBytes") or 0,
                "iris_item_uuid": uuid,
                "iris_resolved": True,
            }
        )
        return (
            item.model_copy(
                update={"artifact_url": resolved_url, "publisher_metadata": metadata}
            ),
            responses,
        )

    @staticmethod
    def _select_bitstream(
        candidates: list[dict[str, Any]], config: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Prefer the configured language edition; never guess across languages."""
        suffix = str(config.get("language_suffix", "-eng")).lower()
        pdfs = [
            candidate
            for candidate in candidates
            if isinstance(candidate.get("uuid"), str)
            and (candidate.get("name") or "").lower().endswith(".pdf")
        ]
        for candidate in pdfs:
            if suffix in (candidate.get("name") or "").lower():
                return candidate
        if config.get("allow_unsuffixed_language", False) and len(pdfs) == 1:
            return pdfs[0]
        return None

    @staticmethod
    def _skip_of(url: str) -> int:
        marker = "$skip="
        index = url.find(marker)
        if index == -1:
            return 0
        tail = url[index + len(marker) :]
        digits = ""
        for character in tail:
            if not character.isdigit():
                break
            digits += character
        return int(digits) if digits else 0


__all__ = ["WHOGuidelinesHubConnector"]
