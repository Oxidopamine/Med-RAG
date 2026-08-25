import io
import json
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula
from pydantic import ValidationError

from app.corpus_steward.cli import materialization_schema_document
from app.corpus_steward.evidence_extractor import XLSX_MEDIA_TYPE, DAKSourceExtractor
from app.corpus_steward.schemas import TrustRootDefinition
from app.schemas.corpus import LocatorKind

MATERIALIZATION_SCHEMA = (
    Path(__file__).resolve().parents[4]
    / "packages"
    / "schemas"
    / "corpus-materialization-result-1.0.0.schema.json"
)
def test_xlsx_extraction_accounts_for_rows_and_cell_anchors() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Decision logic"
    worksheet.append(["Rule", "Action"])
    worksheet.append([])
    worksheet.append(["CD4 < 200", "Start prophylaxis"])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    extracted = DAKSourceExtractor().extract(
        output.getvalue(),
        media_type=XLSX_MEDIA_TYPE,
        source_uri="https://example.test/annex.xlsx",
    )

    assert extracted.expected_source_units == 3
    assert extracted.empty_source_units == 1
    assert len(extracted.units) == 2
    assert extracted.units[1].source_unit_id == "xlsx:Decision logic:row:3"
    assert extracted.units[1].content_exact == "A3=CD4 < 200\nB3=Start prophylaxis"
    assert all(anchor.kind is LocatorKind.TABLE_CELL for anchor in extracted.units[1].anchors)
    assert extracted.units[1].anchors[1].column_index == 1


def test_xlsx_array_formulas_have_stable_exact_content() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Formula"
    worksheet["A1"] = ArrayFormula("A1", "=SUM(B1:B2)")
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    extractor = DAKSourceExtractor()
    first = extractor.extract(
        output.getvalue(), media_type=XLSX_MEDIA_TYPE, source_uri="https://example.test/a.xlsx"
    )
    second = extractor.extract(
        output.getvalue(), media_type=XLSX_MEDIA_TYPE, source_uri="https://example.test/a.xlsx"
    )

    assert first == second
    assert first.units[0].content_exact == "A1==SUM(B1:B2)"
    assert "object at 0x" not in first.units[0].content_exact


def test_trust_root_rejects_combined_global_and_asset_licensing() -> None:
    common = {
        "trust_root_id": "LICENSE_TEST",
        "publisher_id": "PUB_TEST",
        "publisher_name": "Test",
        "allowed_domains": ["example.test"],
        "jurisdictions": ["WORLD"],
        "product_families": ["TEST"],
        "polling_interval_seconds": 3600,
        "connector_name": "synthetic",
        "connector_version": "1.0.0",
        "trusted_stage_key_ids": ["key"],
        "licensing_policy": {
            "license_id": "LEGACY",
            "policy_url": "https://example.test/legacy",
            "acquisition_allowed": True,
        },
        "asset_licensing": [
            {
                "asset_id": "ASSET",
                "asset_kind": "NARRATIVE_SOURCE",
                "license_id": "ASSET-LICENSE",
                "policy_url": "https://example.test/asset",
                "acquisition_allowed": True,
            }
        ],
    }

    with pytest.raises(ValidationError, match="cannot be combined"):
        TrustRootDefinition.model_validate(common)


def test_materialization_schema_is_checked_in() -> None:
    assert json.loads(MATERIALIZATION_SCHEMA.read_text(encoding="utf-8")) == (
        materialization_schema_document()
    )
