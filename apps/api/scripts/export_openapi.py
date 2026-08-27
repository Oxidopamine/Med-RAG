"""Export the FastAPI OpenAPI document for checked-in frontend type generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.main import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" so a Windows export is byte-identical to a Linux one. CI regenerates
    # this file and fails on any diff; without the pin the check passes only because
    # git's autocrlf happens to normalize the CRLF back out, and it breaks the moment
    # someone clones with that setting off.
    output.write_text(
        json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
