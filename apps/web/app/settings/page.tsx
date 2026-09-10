"use client";

import { useState, useSyncExternalStore } from "react";

import content from "@/components/pages/content.module.css";
import { PageFrame } from "@/components/shell/page-frame";
import styles from "@/components/shell/shell.module.css";
import {
  serverSettingsSnapshot,
  settingsSnapshot,
  subscribeSettings,
  writeSettings,
  type ExportFormat,
  type ReaderSettings,
  type TextScale,
} from "@/lib/settings";

const TEXT_SCALES: ReadonlyArray<{ value: TextScale; title: string; description: string }> = [
  { value: "normal", title: "Normal", description: "The interface's usual size." },
  { value: "large", title: "Large", description: "About 12% larger, on every page." },
  { value: "larger", title: "Larger", description: "About 25% larger, on every page." },
];

const EXPORT_FORMATS: ReadonlyArray<{ value: ExportFormat; title: string; description: string }> = [
  {
    value: "print",
    title: "Printable review",
    description: "A PDF made with your browser's print dialog.",
  },
  {
    value: "ris",
    title: "RIS reference file",
    description: "For Zotero, EndNote and other reference managers.",
  },
  {
    value: "bibtex",
    title: "BibTeX",
    description: "For LaTeX and BibTeX-based bibliographies.",
  },
];

export default function SettingsPage() {
  const settings = useSyncExternalStore(subscribeSettings, settingsSnapshot, serverSettingsSnapshot);
  const [organizationsDraft, setOrganizationsDraft] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const organizations = organizationsDraft ?? settings.defaultOrganizations.join(", ");

  function update(patch: Partial<ReaderSettings>) {
    writeSettings({ ...settings, ...patch });
    setSaved(true);
  }

  function commitOrganizations() {
    const list = organizations
      .split(/[,\n]/)
      .map((item) => item.trim().toUpperCase())
      .filter((item, index, all) => item && all.indexOf(item) === index);
    setOrganizationsDraft(null);
    update({ defaultOrganizations: list });
  }

  return (
    <PageFrame
      narrow
      title="Settings"
      lede="Kept in this browser. Nothing on this page leaves the browser or reaches the evidence service."
    >
      <section aria-labelledby="settings-reading" className={styles.card}>
        <h2 id="settings-reading">Reading</h2>
        <div className={styles.field}>
          <fieldset>
            <legend>Text size</legend>
            <div className={content["scale-row"]}>
              <div className={styles.choices}>
                {TEXT_SCALES.map((option) => (
                  <label className={styles.choice} key={option.value}>
                    <input
                      checked={settings.textScale === option.value}
                      name="text-scale"
                      onChange={() => update({ textScale: option.value })}
                      type="radio"
                      value={option.value}
                    />
                    <span className={styles["choice-body"]}>
                      <strong>{option.title}</strong>
                      <span>{option.description}</span>
                    </span>
                  </label>
                ))}
              </div>
              <div className={content["scale-preview"]}>
                <span className={content["scale-preview-label"]}>Preview</span>
                <span className={content["scale-preview-sample"]} data-scale={settings.textScale}>
                  Viral load should be measured twelve months after starting
                  antiretroviral therapy.
                </span>
              </div>
            </div>
          </fieldset>
          <p>Applies to every page, including passages in the source pane.</p>
        </div>
      </section>

      <section aria-labelledby="settings-sources" className={styles.card}>
        <h2 id="settings-sources">Sources</h2>
        <div className={styles.field}>
          <label htmlFor="default-organizations">Default publishers</label>
          <input
            id="default-organizations"
            onBlur={commitOrganizations}
            onChange={(event) => setOrganizationsDraft(event.target.value)}
            placeholder="e.g., WHO"
            type="text"
            value={organizations}
          />
          <p>
            Publisher identifiers to narrow new reviews to, separated by commas. Leave empty to
            search the whole release.
          </p>
        </div>
      </section>

      <section aria-labelledby="settings-export" className={styles.card}>
        <h2 id="settings-export">Export</h2>
        <div className={styles.field}>
          <fieldset>
            <legend>Citation format</legend>
            <div className={styles.choices}>
              {EXPORT_FORMATS.map((option) => (
                <label className={styles.choice} key={option.value}>
                  <input
                    checked={settings.exportFormat === option.value}
                    name="export-format"
                    onChange={() => update({ exportFormat: option.value })}
                    type="radio"
                    value={option.value}
                  />
                  <span className={styles["choice-body"]}>
                    <strong>{option.title}</strong>
                    <span>{option.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <p>The format offered first by the export control on a review.</p>
        </div>
      </section>

      <p className={styles.muted} role="status">
        {saved ? "Saved." : ""}
      </p>
    </PageFrame>
  );
}
