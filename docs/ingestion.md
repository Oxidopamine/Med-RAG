# Authenticated ingestion

The ingestion API is an operator-only boundary. It does not approve a source for retrieval
and it never accepts publisher identity from an acquisition request.

## Configure and migrate

Set a random `INGESTION_API_KEY` of at least 24 characters and keep
`INGESTION_ALLOW_PRIVATE_NETWORKS=false` outside isolated tests. Then apply the schema:

```powershell
.\.venv\Scripts\python -m alembic -c apps\api\alembic.ini upgrade head
```

PDFs are stored below `ARTIFACT_STORE_PATH` using
`<first-two-hash-characters>/<sha256>.pdf`. The artifact path cannot be overwritten with
different content. `data/local/` is excluded from version control.

## Registry and acquisition order

Use `X-Ingestion-Key` on every request below.

1. `POST /v1/ingestion/publishers` with a publisher name and one or more allowed domains.
2. `POST /v1/ingestion/sources` with the publisher ID, metadata, and a canonical HTTPS URL.
3. `POST /v1/ingestion/source-versions` with the source ID and version metadata.
4. `POST /v1/ingestion/source-versions/{id}/acquisitions` with a publisher PDF URL and,
   whenever independently available, `expected_sha256`.

The acquisition response includes the final URL, acquired SHA-256, storage key, extractor
version, page/span counts, diagnostics, and trust status. Native extraction advances only to
`QA_REQUIRED`. Textless, malformed, encrypted, oversized, or hash-mismatched inputs are
quarantined and remain `approved_for_retrieval=false`.

Open quarantine records are available from `GET /v1/ingestion/quarantine`. A reviewed event
can be closed with `POST /v1/ingestion/quarantine/{event_id}/resolve`; resolution records the
reviewer and note but never grants retrieval approval. If no valid artifact exists, resolution
returns the version to `DISCOVERED` for a corrected acquisition. Otherwise it returns to
`QA_REQUIRED` for downstream human QA.
