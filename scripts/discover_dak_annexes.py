"""List the downloadable annexes of a WHO IRIS publication, by handle.

The DAK structural audit (F1/F2 in the README, generalized beyond HIV) needs the *data
dictionary* and *decision-support* workbooks of several DAKs. Those are IRIS bitstreams,
addressed by opaque DSpace UUIDs that cannot be guessed - the HIV connector definition in
`data/trust-roots/who-smart-hiv.json` carries five of them, hand-copied. This resolves
them from a handle instead, so a sibling DAK can be added without transcribing UUIDs.

It only *lists*. Nothing here acquires, hashes, or registers anything: acquisition of
corpus content belongs to the corpus steward's authenticated path, and the audit's own
download step is separate and explicit. Keeping discovery read-only means running this
against an unfamiliar publication cannot quietly pull bytes into the workspace.

Usage:

    python scripts/discover_dak_annexes.py --handle 10665/339745
    python scripts/discover_dak_annexes.py --handle 10665/339745 --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

IRIS = "https://iris.who.int"
TIMEOUT = 60
# IRIS returns HAL+JSON and rejects a bare urllib user agent with 403.
HEADERS = {"Accept": "application/json", "User-Agent": "med-rag-dak-audit/1.0"}


def _get(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def resolve_item(handle: str) -> dict[str, Any]:
    found = _get(f"{IRIS}/server/api/pid/find?id=hdl:{handle}")
    if found.get("type") != "item":
        raise SystemExit(f"handle {handle} resolved to {found.get('type')!r}, not an item")
    return found


def bitstreams(item_uuid: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    bundles = _get(f"{IRIS}/server/api/core/items/{item_uuid}/bundles")
    for bundle in bundles.get("_embedded", {}).get("bundles", []):
        name = bundle.get("name")
        # ORIGINAL holds the published files; THUMBNAIL/LICENSE/TEXT are derived.
        if name != "ORIGINAL":
            continue
        listing = _get(f"{IRIS}/server/api/core/bundles/{bundle['uuid']}/bitstreams?size=200")
        for item in listing.get("_embedded", {}).get("bitstreams", []):
            metadata = item.get("metadata", {})
            mime = ""
            fmt = item.get("_links", {})
            records.append(
                {
                    "name": item.get("name"),
                    "uuid": item.get("uuid"),
                    "size_bytes": item.get("sizeBytes"),
                    "checksum": (item.get("checkSum") or {}).get("value"),
                    "checksum_algorithm": (item.get("checkSum") or {}).get("checkSumAlgorithm"),
                    "description": next(
                        (
                            entry.get("value")
                            for entry in metadata.get("dc.description", [])
                            if entry.get("value")
                        ),
                        None,
                    ),
                    "mime": mime or fmt.get("format", {}).get("href", ""),
                    "url": f"{IRIS}/server/api/core/bitstreams/{item.get('uuid')}/content",
                }
            )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", required=True, help="IRIS handle, e.g. 10665/339745")
    parser.add_argument("--json", type=Path, default=None, help="also write the listing here")
    arguments = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        item = resolve_item(arguments.handle)
        files = bitstreams(item["uuid"])
    except urllib.error.HTTPError as error:
        raise SystemExit(f"IRIS returned {error.code} for handle {arguments.handle}") from error

    print(f"title : {item.get('name')}")
    print(f"item  : {item['uuid']}")
    print(f"files : {len(files)}")
    for record in files:
        size = record["size_bytes"] or 0
        print(f"  {size/1e6:7.2f} MB  {record['name']}")
        print(f"            {record['url']}")

    if arguments.json is not None:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(
            json.dumps(
                {"handle": arguments.handle, "item": item.get("uuid"),
                 "title": item.get("name"), "bitstreams": files},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {arguments.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
