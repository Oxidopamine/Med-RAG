import styles from "./workspace.module.css";

export type DeskTab = "answer" | "evidence" | "checks";

const TABS: Array<{ id: DeskTab; label: string }> = [
  { id: "answer", label: "Answer" },
  { id: "evidence", label: "Evidence" },
  { id: "checks", label: "Checks" },
];

/**
 * The three panes of the desk as tabs, shown only where the desk is one column. The
 * panes stay in the document at every width; the tabs decide which one is on screen.
 */
export function DeskTabs({ active, onChange }: { active: DeskTab; onChange: (tab: DeskTab) => void }) {
  return (
    <div className={styles["desk-tabs"]} role="tablist" aria-label="Review panes">
      {TABS.map((tab) => (
        <button
          aria-controls={`desk-pane-${tab.id}`}
          aria-selected={active === tab.id}
          id={`desk-tab-${tab.id}`}
          key={tab.id}
          onClick={() => onChange(tab.id)}
          role="tab"
          type="button"
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
