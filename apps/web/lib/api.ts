import createClient from "openapi-fetch";

import {
  corpusCatalogueSchema,
  type CorpusCatalogue,
  corpusReadinessSchema,
  questionSummaryListSchema,
  type QuestionSummary,
  parseContract,
  questionAcceptedSchema,
  questionResultSchema,
  tableRowNeighbourhoodSchema,
} from "@/lib/contracts";
import type { paths } from "@/lib/generated/api-schema";
import type {
  ClinicalContext,
  CorpusReadiness,
  QuestionAccepted,
  QuestionResult,
  SourceFilters,
  TableRowNeighbourhood,
} from "@/lib/types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const client = createClient<paths>({ baseUrl: API_URL });

function errorDetail(error: unknown, status: number): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = error.detail;
    if (typeof detail === "string") return detail;
  }
  return `Request failed with status ${status}`;
}

/**
 * How long any single request may hang before it is treated as a dead connection.
 *
 * Every call below is a small JSON round trip against an in-memory record, so this is not
 * a budget for the work - it is the point past which waiting has stopped being waiting. A
 * request that never settles is worse than one that fails: the interface has no way to
 * tell a slow answer from a socket nobody is on the other end of, so it stays busy, keeps
 * its controls locked, and offers the reader nothing to do.
 */
const REQUEST_TIMEOUT_MS = 30_000;

/**
 * Run a request under a deadline, and name a timeout for what it is.
 *
 * The message deliberately says "connection", because that is what a timeout here means
 * and because the workspace's error copy keys on that word to tell the reader the evidence
 * service could not be reached - rather than falling through to the generic failure, which
 * would describe a request that failed rather than one that was never answered.
 */
async function withDeadline<T>(
  what: string,
  run: (signal: AbortSignal) => Promise<T>,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<T> {
  try {
    return await run(AbortSignal.timeout(timeoutMs));
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new Error(
        `The ${what} request timed out after ${Math.round(timeoutMs / 1000)} seconds. ` +
          "The connection to the evidence service did not answer.",
      );
    }
    throw error;
  }
}

const DEFAULT_SOURCE_FILTERS: SourceFilters = {
  jurisdictions: ["US", "EU", "UK"],
  organizations: [],
};

export async function getCorpusReadiness(): Promise<CorpusReadiness> {
  const { data, error, response } = await withDeadline("corpus readiness", (signal) =>
    client.GET("/health/ready", { cache: "no-store", signal }),
  );
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(corpusReadinessSchema, data, "corpus readiness payload");
}

export async function getCorpusCatalogue(): Promise<CorpusCatalogue> {
  const { data, error, response } = await withDeadline("corpus catalogue", (signal) =>
    client.GET("/v1/corpus", { cache: "no-store", signal }),
  );
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(corpusCatalogueSchema, data, "corpus catalogue payload");
}

export async function listQuestions(limit = 50): Promise<QuestionSummary[]> {
  const { data, error, response } = await withDeadline("review list", (signal) =>
    client.GET("/v1/questions", {
      params: { query: { limit } },
      cache: "no-store",
      signal,
    }),
  );
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(questionSummaryListSchema, data, "review list payload");
}

export async function submitQuestion(
  question: string,
  sourceFilters: SourceFilters = DEFAULT_SOURCE_FILTERS,
): Promise<QuestionAccepted> {
  const { data, error, response } = await withDeadline("question submission", (signal) =>
    client.POST("/v1/questions", {
      body: {
        question,
        source_filters: sourceFilters,
        conversation_id: null,
      },
      signal,
    }),
  );
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(questionAcceptedSchema, data, "question acceptance payload");
}

export async function getQuestion(questionId: string): Promise<QuestionResult> {
  const { data, error, response } = await withDeadline("question result", (signal) =>
    client.GET("/v1/questions/{question_id}", {
      params: { path: { question_id: questionId } },
      cache: "no-store",
      signal,
    }),
  );
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(questionResultSchema, data, "question result");
}

/**
 * A rendered source page, with the page box the anchor's coordinates were measured in.
 *
 * `pageWidth` and `pageHeight` are PDF points, not pixels. A `PDF` anchor's bbox is in
 * that same space, so dividing one by the other places the region on the image at
 * whatever size it happens to be displayed - which is why the point box travels with the
 * image rather than the caller having to know the DPI it was rasterised at.
 */
export interface SourcePageImage {
  objectUrl: string;
  pageWidth: number;
  pageHeight: number;
  pageCount: number;
  /**
   * The number the document prints on this page, or null when it defines none.
   *
   * Routinely not the ordinal: front matter runs in roman numerals and an annex can
   * restart at 1, so this is the number a reader will actually find on the paper.
   */
  pageLabel: string | null;
  /** The resolution this copy was rasterised at, so two copies can be told apart. */
  dpi: number;
}

/**
 * What came back when a page was asked for.
 *
 * Three outcomes rather than two, and the distinction is the point. `unavailable` is the
 * ordinary answer - the API returns 404 for every source whose licence withholds page
 * reproduction, which today is all of them - and it means the viewer should draw its
 * located-but-not-reproduced view. `error` means the request itself failed, which is a
 * fault worth naming to the reader and worth offering to retry. Collapsing the two, as a
 * bare `null` did, showed a licence explanation for a dropped connection.
 */
export type SourcePageResult =
  | { status: "loaded"; image: SourcePageImage }
  | { status: "unavailable" }
  | { status: "error"; message: string };

/**
 * How long a page render may take before it is treated as a dead connection.
 *
 * Longer than `REQUEST_TIMEOUT_MS`: the server is rasterising a PDF page rather than
 * reading a record, so slowness here is ordinary in a way it is not for the JSON calls.
 */
const PAGE_REQUEST_TIMEOUT_MS = 60_000;

/** What the API renders at unless asked otherwise; mirrors `DEFAULT_RENDER_DPI`. */
export const DEFAULT_PAGE_DPI = 144;
/** Twice that, for the expanded view, and inside the API's own 300 DPI ceiling. */
export const EXPANDED_PAGE_DPI = 288;

/**
 * How many rendered pages are kept alive at once.
 *
 * Small on purpose. Each entry holds a decoded PNG of a whole page for as long as it is
 * cached, and the access pattern this serves is narrow - stepping down the evidence rail
 * and back up it, which revisits a handful of pages rather than a document.
 */
const PAGE_CACHE_LIMIT = 12;

/**
 * One cached page, and whether anything is still looking at it.
 *
 * `holders` is what stops eviction pulling a blob URL out from under a mounted view.
 * Falling out of the cache and ceasing to be displayed are different events: a reader
 * paging through a long document evicts the page they are still on well before they leave
 * it, and revoking then left an `<img>` pointing at a dead `blob:` URL while its owner
 * still reported the page as loaded - a broken image with neither a retry nor the
 * licence view, because nothing in the state said anything had gone wrong. An evicted
 * entry with holders is `discarded` instead, and released by the last one to let go.
 */
interface PageCacheEntry {
  pending: Promise<SourcePageResult>;
  holders: number;
  /** Out of the cache, kept alive only for whoever is still displaying it. */
  discarded: boolean;
}

const pageCache = new Map<string, PageCacheEntry>();

function pageCacheKey(sourceId: string, pageNumber: number, dpi: number): string {
  return `${sourceId}|${pageNumber}|${dpi}`;
}

function revokePage(entry: PageCacheEntry): void {
  void entry.pending
    .then((result) => {
      if (result.status === "loaded") URL.revokeObjectURL(result.image.objectUrl);
    })
    .catch(() => {
      // A rejected entry allocated no blob URL, so there is nothing to release.
    });
}

/** Drop an entry from the cache, freeing its blob unless something still displays it. */
function discardPage(entry: PageCacheEntry): void {
  entry.discarded = true;
  if (entry.holders === 0) revokePage(entry);
}

/**
 * Keep a cached page alive for as long as the caller may still be drawing it.
 *
 * Advisory over the cache rather than a second way into it: it retains an entry that is
 * already there and does nothing when there is not one, so a caller retains immediately
 * after the `getSourcePageImage` that created the entry. Returns the release, which is
 * idempotent because React will run an effect's cleanup on paths that also unmount.
 */
export function retainSourcePage(
  sourceId: string,
  pageNumber: number,
  { dpi = DEFAULT_PAGE_DPI }: { dpi?: number } = {},
): () => void {
  const entry = pageCache.get(pageCacheKey(sourceId, pageNumber, dpi));
  if (entry === undefined) return () => {};

  entry.holders += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    entry.holders -= 1;
    if (entry.holders === 0 && entry.discarded) revokePage(entry);
  };
}

/**
 * One page image, from the cache when it is there and from the API when it is not.
 *
 * Keyed by source, page and DPI, and shared while in flight, so walking the evidence rail
 * back onto a page already seen costs nothing and pressing `j` twice quickly makes one
 * request rather than two. A blob URL lives exactly as long as its cache entry, which is
 * why a caller must not revoke what it is handed: the cache owns it, and a caller that
 * released it would break the next reader of the same page.
 *
 * There is deliberately no abort signal. The unit of work is a page the cache wants
 * anyway, so a reader who moves on before it arrives has paid for their next visit rather
 * than wasted a request, and a stale result is dropped by the caller comparing keys.
 */
export function getSourcePageImage(
  sourceId: string,
  pageNumber: number,
  { dpi = DEFAULT_PAGE_DPI }: { dpi?: number } = {},
): Promise<SourcePageResult> {
  const key = pageCacheKey(sourceId, pageNumber, dpi);
  const cached = pageCache.get(key);
  if (cached) {
    // Re-inserted so the map's insertion order stays a least-recently-used order, which
    // is what makes the eviction below drop the page nobody has looked at.
    pageCache.delete(key);
    pageCache.set(key, cached);
    return cached.pending;
  }

  const entry: PageCacheEntry = {
    pending: fetchSourcePage(sourceId, pageNumber, dpi).then((result) => {
      // A failure is not kept. That the licence withholds a page, and that a document has
      // no such page, are stable facts about the corpus and cache cleanly; a dropped
      // connection is neither, and caching it would make the offered retry a no-op.
      if (result.status === "error" && pageCache.get(key) === entry) {
        pageCache.delete(key);
        entry.discarded = true;
      }
      return result;
    }),
    holders: 0,
    discarded: false,
  };

  pageCache.set(key, entry);
  while (pageCache.size > PAGE_CACHE_LIMIT) {
    const oldest = pageCache.keys().next().value;
    if (oldest === undefined) break;
    const evicted = pageCache.get(oldest)!;
    pageCache.delete(oldest);
    discardPage(evicted);
  }
  return entry.pending;
}

async function fetchSourcePage(
  sourceId: string,
  pageNumber: number,
  dpi: number,
): Promise<SourcePageResult> {
  const path = `/v1/sources/${encodeURIComponent(sourceId)}/pages/${pageNumber}`;
  const query = dpi === DEFAULT_PAGE_DPI ? "" : `?dpi=${dpi}`;

  let response: Response;
  try {
    // A deadline, not a cancellation. Nothing here aborts because the reader moved on -
    // see `getSourcePageImage` for why that would waste a page the cache wants anyway -
    // but a request that never settles is a different thing entirely: the figure holds its
    // loading state off this promise, so a hung socket is a spinner that never stops.
    // Longer than the JSON deadline because the server is rasterising a page to do it.
    response = await fetch(`${API_URL}${path}${query}`, {
      signal: AbortSignal.timeout(PAGE_REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      return { status: "error", message: "The source page took too long to arrive." };
    }
    return { status: "error", message: "The source page could not be reached." };
  }

  if (response.status === 404) return { status: "unavailable" };
  if (!response.ok) {
    return {
      status: "error",
      message: `The source page request failed with status ${response.status}.`,
    };
  }

  const width = Number(response.headers.get("X-Page-Width"));
  const height = Number(response.headers.get("X-Page-Height"));
  const count = Number(response.headers.get("X-Page-Count"));
  const label = response.headers.get("X-Page-Label");

  let blob: Blob;
  try {
    blob = await response.blob();
  } catch {
    return { status: "error", message: "The source page did not download completely." };
  }

  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    // Without the page box the image cannot carry a placed region, and an image with a
    // rectangle in the wrong place is worse than no image. Reported as an error rather
    // than as an absent page: the source did permit reproduction, and something between
    // here and there dropped the header.
    return {
      status: "error",
      message: "The source page arrived without the page box its highlights need.",
    };
  }

  return {
    status: "loaded",
    image: {
      objectUrl: URL.createObjectURL(blob),
      pageWidth: width,
      pageHeight: height,
      pageCount: Number.isFinite(count) ? count : 0,
      pageLabel: label !== null && label.trim() !== "" ? label.trim() : null,
      dpi,
    },
  };
}

/**
 * The rows of a table immediately around a cited one.
 *
 * A spreadsheet row is this corpus's unit of evidence, and one row read alone is often
 * not decidable - the row above opens the condition, the row below carries the exception.
 * The API answers with an empty window rather than a status for every case it will not
 * serve, so an empty `rows` means "nothing to show", never "something went wrong".
 */
export async function getTableRowNeighbourhood(
  sourceId: string,
  tableId: string,
  rowIndex: number,
  radius = 3,
): Promise<TableRowNeighbourhood> {
  const { data, error, response } = await withDeadline("table row", (signal) =>
    client.GET("/v1/sources/{source_id}/tables/{table_id}/rows", {
      params: {
        path: { source_id: sourceId, table_id: tableId },
        query: { row: rowIndex, radius },
      },
      signal,
    }),
  );
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(tableRowNeighbourhoodSchema, data, "table row neighbourhood");
}

export async function replaceQuestionContext(
  questionId: string,
  context: ClinicalContext,
): Promise<QuestionAccepted> {
  const { data, error, response } = await withDeadline("context update", (signal) =>
    client.PATCH("/v1/questions/{question_id}/context", {
      params: { path: { question_id: questionId } },
      body: { context },
      signal,
    }),
  );
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(questionAcceptedSchema, data, "context update response");
}
