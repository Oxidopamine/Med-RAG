"use client";

import { useState, useSyncExternalStore } from "react";

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
      lede="Kept in this browser. Nothing here is sent to the evidence service."
    >
      <section aria-labelledby="settings-reading">
        <h2 id="settings-reading">Reading</h2>
        <div className={styles.field}>
          <label htmlFor="text-scale">Text size</label>
          <select
            id="text-scale"
            value={settings.textScale}
            onChange={(event) => update({ textScale: event.target.value as TextScale })}
          >
            <option value="normal">Normal</option>
            <option value="large">Large</option>
            <option value="larger">Larger</option>
          </select>
          <p>Applies to every page, including passages in the source pane.</p>
        </div>
      </section>

      <section aria-labelledby="settings-sources">
        <h2 id="settings-sources">Sources</h2>
        <div className={styles.field}>
          <label htmlFor="default-organizations">Default publishers</label>
          <input
            id="default-organizations"
            type="text"
            value={organizations}
            placeholder="e.g., WHO"
            onChange={(event) => setOrganizationsDraft(event.target.value)}
            onBlur={commitOrganizations}
          />
          <p>
            Publisher identifiers to narrow new reviews to, separated by commas. Leave empty to
            search the whole release.
          </p>
        </div>
      </section>

      <section aria-labelledby="settings-export">
        <h2 id="settings-export">Export</h2>
        <div className={styles.field}>
          <fieldset>
            <legend>Citation format</legend>
            {(
              [
                ["print", "Printable review (PDF via the browser)"],
                ["ris", "RIS reference file"],
                ["bibtex", "BibTeX"],
              ] as const
            ).map(([value, label]) => (
              <label key={value}>
                <input
                  checked={settings.exportFormat === value}
                  name="export-format"
                  type="radio"
                  value={value}
                  onChange={() => update({ exportFormat: value as ExportFormat })}
                />
                {label}
              </label>
            ))}
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
