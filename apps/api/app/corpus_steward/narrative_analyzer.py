"""The narrative document processor: a structural census of a guideline PDF.

Pure and dependency-free by design - it takes bytes and returns a
`NarrativeDocumentAnalysis`. No database, no signing, no persistence. The service that
wraps it supplies those, exactly as `StructuredPackageService` wraps `FHIRPackageParser`.

## Why it reuses the evidence extractor

The unit inventory this stage signs is only worth signing if materialization can recompute
it and disagree. That requires both stages to enumerate units the same way, so the census
runs `DAKSourceExtractor` rather than reimplementing page enumeration. The FHIR path has
the identical relationship - `structured_service` and materialization both go through
`FHIRPackageParser` - and the check still catches the failures it exists for: bytes that
changed between stages, an extractor version that moved underneath a signed report, or a
materialization run pointed at a different artifact.

What it does *not* do is retain any clinical text. `unit_inventory_sha256` is a digest over
ordered `(source_unit_id, sha256(content_exact))` pairs; the text is hashed and dropped.
That is what lets this report be stored and attested without becoming a redistribution of
the source.

## The checks are deliberately asymmetric about silence

A document that *declares* encryption, embedded files, or JavaScript is a BLOCK: those are
positive statements about content that has no business in a guideline corpus. A document
that declares *no* licence is a WARN, not a BLOCK - absence of an XMP rights statement is
common in published PDFs and is not a contradiction of the operator's policy. A declared
licence that disagrees with the operator's policy is a BLOCK, because that is a genuine
conflict between two claims about the same asset.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import pymupdf

from app.corpus_steward.evidence_extractor import (
    DAKSourceExtractor,
    EvidenceExtractionError,
)
from app.corpus_steward.narrative_schemas import (
    NarrativeDocumentAnalysis,
    NarrativeDocumentDeclaration,
)
from app.schemas.corpus import canonical_sha256

PDF_MEDIA_TYPE = "application/pdf"

# XMP rights, read as declarations rather than parsed as a licence grammar. A publisher
# writes these free-form, so the identifier is matched against known SPDX-ish spellings and
# the full statement is always carried alongside for a human to read.
_XMP_RIGHTS = re.compile(
    r"<(?:dc:rights|xmpRights:WebStatement)[^>]*>(.*?)</(?:dc:rights|xmpRights:WebStatement)>",
    re.DOTALL | re.IGNORECASE,
)
_XMP_TEXT = re.compile(r"<[^>]+>")
# Matched as an ordered set of *restriction* tokens rather than by a loose pattern per
# licence. An earlier version used `\bby\b.{0,12}4\.0`, which matched the "by" inside
# "by-nc" and identified CC BY-NC 4.0 as permissive CC-BY-4.0 - the single worst direction
# for this function to be wrong in, because `declared_license_id` feeds the LICENSE_POLICY
# cross-check and a NonCommercial document would have been compared as if it were open.
_CC_MARKER = re.compile(r"\b(?:cc|creative\s+commons)\b|attribution", re.IGNORECASE)
_CC_VERSION = re.compile(r"\b([34])\.0\b")
_CC_IGO = re.compile(r"\bigo\b", re.IGNORECASE)
# Both the short ("nc", "sa", "nd") and long ("noncommercial", "sharealike") spellings;
# WHO IRIS PDFs commonly write dc:rights in the long form.
_CC_RESTRICTIONS: tuple[tuple[str, str], ...] = (
    ("NC", r"\bnc\b|non[\s-]*commercial"),
    ("ND", r"\bnd\b|no[\s-]*deriv\w*"),
    ("SA", r"\bsa\b|share[\s-]*alike"),
)


class NarrativeAnalysisError(Exception):
    """A narrative document could not be analysed at all."""

    def __init__(self, reason_code: str, details: str) -> None:
        super().__init__(details)
        self.reason_code = reason_code
        self.details = details


@dataclass(frozen=True)
class DocumentSafetyFindings:
    """Positive statements the document makes that a corpus should refuse."""

    encrypted: bool
    has_embedded_files: bool
    has_javascript: bool

    @property
    def unsafe(self) -> tuple[str, ...]:
        found = []
        if self.encrypted:
            found.append("ENCRYPTED")
        if self.has_embedded_files:
            found.append("EMBEDDED_FILES")
        if self.has_javascript:
            found.append("JAVASCRIPT")
        return tuple(found)


def _license_identifier(statement: str) -> str | None:
    """A conservative SPDX-ish identifier for a Creative Commons rights statement.

    Returns None rather than guessing. A wrong identifier is worse than no identifier here:
    the LICENSE_POLICY check treats silence as a WARN but a *conflicting* declaration as a
    BLOCK, so an unrecognised statement fails toward review while a misread one could pass
    a restrictive document as permissive.
    """

    collapsed = " ".join(statement.split())
    if not _CC_MARKER.search(collapsed):
        return None
    version = _CC_VERSION.search(collapsed)
    if version is None:
        return None
    parts = ["CC", "BY"]
    parts.extend(
        token
        for token, pattern in _CC_RESTRICTIONS
        if re.search(pattern, collapsed, re.IGNORECASE)
    )
    parts.append(f"{version.group(1)}.0")
    if _CC_IGO.search(collapsed):
        parts.append("IGO")
    return "-".join(parts)


def _read_declaration(document: pymupdf.Document, page_count: int) -> NarrativeDocumentDeclaration:
    metadata = document.metadata or {}
    statement: str | None = None
    try:
        raw_xmp = document.xref_xml_metadata()
    except Exception:  # pragma: no cover - absent XMP is the common case
        raw_xmp = None
    # Bindings differ: this returns str, bytes, or an int xref depending on version and
    # document. Anything that is not text carries no rights statement to read.
    if isinstance(raw_xmp, bytes):
        xmp = raw_xmp.decode("utf-8", errors="replace")
    elif isinstance(raw_xmp, str):
        xmp = raw_xmp
    else:
        xmp = ""
    if xmp:
        match = _XMP_RIGHTS.search(xmp)
        if match:
            text = " ".join(_XMP_TEXT.sub(" ", match.group(1)).split())
            statement = text or None
    return NarrativeDocumentDeclaration(
        pdf_version=str(metadata.get("format") or "") or None,
        encrypted=bool(document.is_encrypted or document.needs_pass),
        has_embedded_files=_embedded_file_count(document) > 0,
        has_javascript=_has_javascript(document),
        page_count=page_count,
        title=str(metadata.get("title") or "") or None,
        producer=str(metadata.get("producer") or "") or None,
        creation_date=str(metadata.get("creationDate") or "") or None,
        declared_license_id=_license_identifier(statement) if statement else None,
        declared_license_statement=statement,
    )


def _embedded_file_count(document: pymupdf.Document) -> int:
    try:
        return int(document.embfile_count())
    except Exception:  # pragma: no cover - older bindings
        return 0


def _has_javascript(document: pymupdf.Document) -> bool:
    """Whether the catalog declares document-level JavaScript.

    Read from the trailer catalog rather than by scanning bytes: the string ``/JavaScript``
    appears inside perfectly ordinary compressed streams, and a byte scan would refuse
    clean documents.
    """

    try:
        kind, _ = document.xref_get_key(-1, "Names/JavaScript")
    except Exception:  # pragma: no cover - bindings without catalog access
        return False
    return kind not in (None, "null")


def analyse_narrative_document(
    content: bytes,
    *,
    asset_id: str,
    artifact_sha256: str,
    media_type: str,
    source_uri: str,
    extractor: DAKSourceExtractor | None = None,
) -> tuple[NarrativeDocumentAnalysis, DocumentSafetyFindings]:
    """Census one narrative asset. Returns the analysis and its safety findings."""

    if media_type != PDF_MEDIA_TYPE:
        raise NarrativeAnalysisError(
            "UNSUPPORTED_NARRATIVE_MEDIA_TYPE",
            f"narrative analysis supports {PDF_MEDIA_TYPE}, got {media_type}",
        )
    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception as error:
        raise NarrativeAnalysisError("PDF_OPEN_FAILED", str(error)) from error
    try:
        page_count = int(document.page_count)
        declaration = _read_declaration(document, page_count)
    finally:
        document.close()

    safety = DocumentSafetyFindings(
        encrypted=declaration.encrypted,
        has_embedded_files=declaration.has_embedded_files,
        has_javascript=declaration.has_javascript,
    )

    unit_count = 0
    inventory_digest: str | None = None
    if not safety.unsafe:
        # Only enumerate units for a document that passed safety. Running an extractor over
        # bytes already judged unsafe is the thing the safety check exists to prevent.
        try:
            extracted = (extractor or DAKSourceExtractor()).extract(
                content, media_type=media_type, source_uri=source_uri
            )
        except EvidenceExtractionError as error:
            raise NarrativeAnalysisError(error.reason_code, str(error)) from error
        pairs = [
            {
                "source_unit_id": unit.source_unit_id,
                "content_sha256": hashlib.sha256(
                    unit.content_exact.encode("utf-8")
                ).hexdigest(),
            }
            for unit in extracted.units
        ]
        unit_count = len(pairs)
        inventory_digest = canonical_sha256({"units": pairs}) if pairs else None

    analysis = NarrativeDocumentAnalysis(
        asset_id=asset_id,
        artifact_sha256=artifact_sha256,
        media_type=media_type,
        byte_size=len(content),
        unit_count=unit_count,
        unit_inventory_sha256=inventory_digest,
        declaration=declaration,
    )
    return analysis, safety
