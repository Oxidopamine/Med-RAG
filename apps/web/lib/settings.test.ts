import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_SETTINGS,
  applyTextScale,
  readSettings,
  settingsSnapshot,
  subscribeSettings,
  writeSettings,
} from "./settings";

afterEach(() => {
  window.localStorage.clear();
  applyTextScale("normal");
});

describe("reader settings", () => {
  it("reads the defaults when nothing is stored or the store is malformed", () => {
    expect(readSettings()).toEqual(DEFAULT_SETTINGS);
    window.localStorage.setItem("sentinel-rag.settings.v1", "{not json");
    expect(readSettings()).toEqual(DEFAULT_SETTINGS);
    window.localStorage.setItem(
      "sentinel-rag.settings.v1",
      JSON.stringify({ textScale: "huge", exportFormat: 7, defaultOrganizations: ["WHO", 3] }),
    );
    expect(readSettings()).toEqual({ ...DEFAULT_SETTINGS, defaultOrganizations: ["WHO"] });
  });

  it("round-trips a write, stamps the text scale on the root, and notifies subscribers", () => {
    let notified = 0;
    const unsubscribe = subscribeSettings(() => {
      notified += 1;
    });
    writeSettings({ textScale: "large", defaultOrganizations: ["WHO"], exportFormat: "ris" });
    expect(readSettings()).toEqual({
      textScale: "large",
      defaultOrganizations: ["WHO"],
      exportFormat: "ris",
    });
    expect(settingsSnapshot().textScale).toBe("large");
    expect(document.documentElement.dataset.textScale).toBe("large");
    expect(notified).toBe(1);
    unsubscribe();
    writeSettings(DEFAULT_SETTINGS);
    expect(document.documentElement.dataset.textScale).toBeUndefined();
    expect(notified).toBe(1);
  });
});
