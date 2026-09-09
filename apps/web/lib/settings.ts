/**
 * Reader settings, kept in this browser.
 *
 * Text size used to be a stepper inside the source inspector, which made an app-wide
 * preference look like a property of one panel. It lives here now, applied to the root
 * element so every page follows it, and the inspector reads it like everything else.
 */

export type TextScale = "normal" | "large" | "larger";
export type ExportFormat = "print" | "ris" | "bibtex";

export interface ReaderSettings {
  textScale: TextScale;
  /** Publisher identifiers to narrow retrieval to by default; empty means the release. */
  defaultOrganizations: string[];
  exportFormat: ExportFormat;
}

export const DEFAULT_SETTINGS: ReaderSettings = {
  textScale: "normal",
  defaultOrganizations: [],
  exportFormat: "print",
};

const STORAGE_KEY = "sentinel-rag.settings.v1";
const TEXT_SCALES: readonly TextScale[] = ["normal", "large", "larger"];
const EXPORT_FORMATS: readonly ExportFormat[] = ["print", "ris", "bibtex"];

export function readSettings(): ReaderSettings {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return DEFAULT_SETTINGS;
    const record = parsed as Record<string, unknown>;
    const textScale = TEXT_SCALES.includes(record.textScale as TextScale)
      ? (record.textScale as TextScale)
      : DEFAULT_SETTINGS.textScale;
    const exportFormat = EXPORT_FORMATS.includes(record.exportFormat as ExportFormat)
      ? (record.exportFormat as ExportFormat)
      : DEFAULT_SETTINGS.exportFormat;
    const defaultOrganizations = Array.isArray(record.defaultOrganizations)
      ? record.defaultOrganizations.filter((item): item is string => typeof item === "string")
      : DEFAULT_SETTINGS.defaultOrganizations;
    return { textScale, exportFormat, defaultOrganizations };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function writeSettings(settings: ReaderSettings): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Storage can be unavailable; the setting still applies for this page.
  }
  cached = settings;
  applyTextScale(settings.textScale);
  for (const listener of listeners) listener();
}

/*
 * An external store, so a page can read settings with `useSyncExternalStore` and
 * render the stored value on the client without a state update inside an effect.
 * The server snapshot is the default; the client snapshot is read once and cached.
 */
let cached: ReaderSettings | null = null;
const listeners = new Set<() => void>();

export function settingsSnapshot(): ReaderSettings {
  if (cached === null) cached = readSettings();
  return cached;
}

export function serverSettingsSnapshot(): ReaderSettings {
  return DEFAULT_SETTINGS;
}

export function subscribeSettings(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Stamp the scale on the root so the stylesheet can size everything from it. */
export function applyTextScale(scale: TextScale): void {
  if (typeof document === "undefined") return;
  if (scale === "normal") {
    delete document.documentElement.dataset.textScale;
  } else {
    document.documentElement.dataset.textScale = scale;
  }
}
