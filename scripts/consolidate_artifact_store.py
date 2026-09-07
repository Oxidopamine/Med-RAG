"""Merge a forked steward artifact store into the canonical one.

`STEWARD_ARTIFACT_STORE_PATH` was relative, so `corpus-steward` run from `apps/api` and the
API server run from the repo root resolved it to two different directories. The database is
shared and addresses artifacts by digest, so nothing failed at write time - a release's
evidence simply became unreadable to the process meant to serve it.

The store is content-addressed: a blob's filename is the SHA-256 of its own bytes. Merging
is therefore a union, and a digest present in both stores must have identical content. This
verifies that rather than assuming it, and refuses to overwrite on any mismatch.

    python scripts/consolidate_artifact_store.py <source-store> <canonical-store> [--apply]
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("canonical", type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="copy; otherwise report only"
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"nothing to merge: {args.source} does not exist")
        return 0

    blobs = sorted(args.source.rglob("*.blob"))
    print(f"source {args.source}: {len(blobs)} blob(s)")

    copied = skipped = corrupt = conflict = 0
    for blob in blobs:
        payload = blob.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != blob.stem:
            # A blob whose name is not its own digest is not a content-addressed artifact.
            # Refuse it rather than copying something the store would then vouch for.
            print(f"  CORRUPT {blob.name[:16]}… name does not match its content digest")
            corrupt += 1
            continue
        target = args.canonical / "sha256" / digest[:2] / f"{digest}.blob"
        if target.exists():
            if target.read_bytes() != payload:
                print(f"  CONFLICT {digest[:16]}… same digest, different bytes")
                conflict += 1
            else:
                skipped += 1
            continue
        if args.apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(blob, target)
        copied += 1

    verb = "copied" if args.apply else "would copy"
    print(
        f"\n{verb}: {copied}   already present: {skipped}   corrupt: {corrupt}   conflicts: {conflict}"
    )
    if corrupt or conflict:
        print("refusing to treat this as a clean merge")
        return 1
    if not args.apply:
        print("dry run - pass --apply to perform the merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
