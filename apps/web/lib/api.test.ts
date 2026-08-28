import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_PAGE_DPI,
  EXPANDED_PAGE_DPI,
  getSourcePageImage,
  retainSourcePage,
} from "@/lib/api";

/**
 * The page-image client, which is the only part of the API surface that holds state.
 *
 * The cache is module-scoped and there is deliberately no way to clear it: a reset hook
 * would be production code that exists only for these tests, and reaching for
 * `vi.resetModules()` instead made the whole module graph re-resolve outside Vite's alias
 * plugin - which passed when this file ran alone and failed the moment another file ran
 * first. Isolation comes from the cache key instead. Every test uses a source nothing else
 * uses, so no test can be answered from another's entry, which is the property module
 * isolation was there to give.
 */
let sources = 0;
function uniqueSource(): string {
  sources += 1;
  return `SRC_${sources}`;
}

function pageResponse({
  headers = {},
  status = 200,
}: {
  headers?: Record<string, string>;
  status?: number;
} = {}) {
  const merged: Record<string, string> = {
    "X-Page-Width": "612",
    "X-Page-Height": "792",
    "X-Page-Count": "160",
    ...headers,
  };
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => merged[name] ?? null },
    blob: async () => new Blob(["png"], { type: "image/png" }),
  } as unknown as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getSourcePageImage, what came back", () => {
  it("reports a rendered page with the point box its highlights need", async () => {
    fetchMock.mockResolvedValue(pageResponse({ headers: { "X-Page-Label": "xiv" } }));

    const result = await getSourcePageImage(uniqueSource(), 14);

    expect(result).toEqual({
      status: "loaded",
      image: {
        objectUrl: "blob:test",
        pageWidth: 612,
        pageHeight: 792,
        pageCount: 160,
        pageLabel: "xiv",
        dpi: DEFAULT_PAGE_DPI,
      },
    });
  });

  it("has no label to report when the document defines none", async () => {
    fetchMock.mockResolvedValue(pageResponse({ headers: { "X-Page-Label": "  " } }));

    const result = await getSourcePageImage(uniqueSource(), 14);

    expect(result).toMatchObject({ status: "loaded", image: { pageLabel: null } });
  });

  /*
   * The distinction the viewer is built on. A 404 is the ordinary answer for every source
   * whose licence withholds page reproduction; anything else is a fault, and telling a
   * reader the licence withheld a page when the connection dropped is a claim about a
   * publisher this interface has no basis to make.
   */
  it("calls a withheld page unavailable, not an error", async () => {
    fetchMock.mockResolvedValue(pageResponse({ status: 404 }));

    expect(await getSourcePageImage(uniqueSource(), 14)).toEqual({ status: "unavailable" });
  });

  it("calls a failed request an error, and names it", async () => {
    fetchMock.mockResolvedValue(pageResponse({ status: 503 }));

    expect(await getSourcePageImage(uniqueSource(), 14)).toEqual({
      status: "error",
      message: "The source page request failed with status 503.",
    });
  });

  it("calls an unreachable API an error rather than throwing at the caller", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    expect(await getSourcePageImage(uniqueSource(), 14)).toEqual({
      status: "error",
      message: "The source page could not be reached.",
    });
  });

  it("refuses a page that arrived without its page box", async () => {
    fetchMock.mockResolvedValue(pageResponse({ headers: { "X-Page-Width": "" } }));

    // An image with a rectangle in the wrong place is worse than no image, and this is a
    // fault rather than a licence answer: the source did permit reproduction.
    expect(await getSourcePageImage(uniqueSource(), 14)).toMatchObject({ status: "error" });
  });

  it("asks for a resolution only when it is not the default", async () => {
    const source = uniqueSource();
    fetchMock.mockResolvedValue(pageResponse());

    await getSourcePageImage(source, 14);
    expect(fetchMock.mock.calls[0]![0]).toBe(
      `http://localhost:8000/v1/sources/${source}/pages/14`,
    );

    await getSourcePageImage(source, 14, { dpi: EXPANDED_PAGE_DPI });
    expect(fetchMock.mock.calls[1]![0]).toBe(
      `http://localhost:8000/v1/sources/${source}/pages/14?dpi=288`,
    );
  });
});

describe("getSourcePageImage, the cache", () => {
  it("serves a page already fetched without asking again", async () => {
    const source = uniqueSource();
    fetchMock.mockResolvedValue(pageResponse());

    await getSourcePageImage(source, 14);
    await getSourcePageImage(source, 15);
    await getSourcePageImage(source, 14);

    // Walking the evidence rail down and back up revisits pages; it should not re-download
    // and re-decode a full page image to do it.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("makes one request when the same page is asked for twice at once", async () => {
    const source = uniqueSource();
    fetchMock.mockResolvedValue(pageResponse());

    const [first, second] = await Promise.all([
      getSourcePageImage(source, 14),
      getSourcePageImage(source, 14),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(first).toBe(second);
  });

  it("keeps resolutions of one page apart", async () => {
    const source = uniqueSource();
    fetchMock.mockResolvedValue(pageResponse());

    await getSourcePageImage(source, 14);
    await getSourcePageImage(source, 14, { dpi: EXPANDED_PAGE_DPI });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("caches a withheld page, because that answer does not change", async () => {
    const source = uniqueSource();
    fetchMock.mockResolvedValue(pageResponse({ status: 404 }));

    await getSourcePageImage(source, 14);
    await getSourcePageImage(source, 14);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not cache a failure, so the offered retry is not a no-op", async () => {
    const source = uniqueSource();
    fetchMock.mockResolvedValueOnce(pageResponse({ status: 503 }));
    fetchMock.mockResolvedValue(pageResponse());

    expect(await getSourcePageImage(source, 14)).toMatchObject({ status: "error" });
    expect(await getSourcePageImage(source, 14)).toMatchObject({ status: "loaded" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("releases the pages it evicts and keeps the ones still in reach", async () => {
    const source = uniqueSource();
    let issued = 0;
    const revoke = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL: () => {
        issued += 1;
        return `blob:${source}-${issued}`;
      },
      revokeObjectURL: revoke,
    });
    fetchMock.mockResolvedValue(pageResponse());

    // The cache holds twelve. Thirteen pages from one source therefore push the first of
    // them out however many entries other tests left behind - which is what makes this
    // assertion about the first page, rather than about the number of evictions.
    for (let page = 1; page <= 13; page += 1) {
      await getSourcePageImage(source, page);
    }

    expect(fetchMock).toHaveBeenCalledTimes(13);
    expect(revoke).toHaveBeenCalledWith(`blob:${source}-1`);
    expect(revoke).not.toHaveBeenCalledWith(`blob:${source}-13`);

    // The most recent page is still held, so revisiting it costs nothing.
    await getSourcePageImage(source, 13);
    expect(fetchMock).toHaveBeenCalledTimes(13);
  });

  /*
   * Eviction and being on screen are different events, and the second outlives the first.
   *
   * A reader paging through a long document evicts the page they are still looking at well
   * before they leave it. Revoking then left the `<img>` pointing at a dead `blob:` URL
   * while its owner still reported the page as loaded - a broken image with neither a retry
   * nor the licence view, because nothing in the state said anything had gone wrong.
   */
  it("keeps an evicted page alive while something is still displaying it", async () => {
    const source = uniqueSource();
    let issued = 0;
    const revoke = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL: () => {
        issued += 1;
        return `blob:${source}-${issued}`;
      },
      revokeObjectURL: revoke,
    });
    fetchMock.mockResolvedValue(pageResponse());

    await getSourcePageImage(source, 1);
    const release = retainSourcePage(source, 1);

    // Far enough past the cache limit that page 1 is certainly gone from it.
    for (let page = 2; page <= 20; page += 1) {
      await getSourcePageImage(source, page);
    }

    expect(revoke).not.toHaveBeenCalledWith(`blob:${source}-1`);

    // Let go, and the page that no longer has a place in the cache is freed.
    release();
    await Promise.resolve();
    expect(revoke).toHaveBeenCalledWith(`blob:${source}-1`);
  });

  it("frees a retained page only once, however often it is released", async () => {
    const source = uniqueSource();
    let issued = 0;
    const revoke = vi.fn();
    vi.stubGlobal("URL", {
      // One URL per page, so what is counted below is this page's releases and not the
      // evictions of the nineteen pages that push it out of the cache.
      createObjectURL: () => {
        issued += 1;
        return `blob:${source}-${issued}`;
      },
      revokeObjectURL: revoke,
    });
    fetchMock.mockResolvedValue(pageResponse());

    await getSourcePageImage(source, 1);
    const release = retainSourcePage(source, 1);
    for (let page = 2; page <= 20; page += 1) {
      await getSourcePageImage(source, page);
    }

    release();
    release();
    await Promise.resolve();
    expect(revoke.mock.calls.filter(([url]) => url === `blob:${source}-1`)).toHaveLength(1);
  });
});
