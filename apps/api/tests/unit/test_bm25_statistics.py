import json
from pathlib import Path

from app.corpus_steward.bm25_statistics import derive_bm25_release_statistics
from app.corpus_steward.cli import bm25_statistics_schema_document
from app.corpus_steward.embedding_adapters import (
    BM25AdapterParameters,
    unicode_medical_tokens,
)
from app.schemas.corpus import CorpusReleaseBundle

ROOT = Path(__file__).parents[4]
FIXTURE_PATH = ROOT / "data" / "fixtures" / "corpus-release-v1.json"
SCHEMA_PATH = ROOT / "packages" / "schemas" / "bm25-release-statistics-1.0.0.schema.json"


def parameters() -> BM25AdapterParameters:
    return BM25AdapterParameters(
        tokenizer="unicode-medical-v1",
        k1=1.2,
        b=0.75,
        average_document_length=1.0,
        query_term_frequency="binary",
        hash_algorithm="sha256-uint32-le",
        hash_seed="fixture",
        query_prefix="",
        document_prefix="",
        stopwords_path=None,
    )


def test_bm25_statistics_are_release_derived_and_digest_sealed() -> None:
    bundle = CorpusReleaseBundle.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )
    result = derive_bm25_release_statistics(bundle, parameters())
    lengths = [len(unicode_medical_tokens(item.content_search)) for item in bundle.evidence]

    assert result.content.document_count == len(bundle.evidence)
    assert result.content.total_token_count == sum(lengths)
    assert result.content.average_document_length == sum(lengths) / len(lengths)
    assert result.content.manifest_sha256 == bundle.manifest.manifest_sha256


def test_checked_in_bm25_statistics_schema_matches_contract() -> None:
    assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == (
        bm25_statistics_schema_document()
    )
