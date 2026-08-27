import { Crosshair, FileLock2, FileText, Grid3x3, MapPin, Quote, ScanLine } from "lucide-react";

import type { AnchorPrecision, RenderPolicy, SourceAnchor } from "@/lib/evidence-presentation";
import {
  anchorRegionStatus,
  humanizeCode,
  renderPolicy,
  sourceAnchors,
} from "@/lib/evidence-presentation";
import type { EvidenceDetail } from "@/lib/types";

import styles from "./workspace.module.css";

const PRECISION_COPY: Record<AnchorPrecision, { icon: typeof MapPin; label: string }> = {
  EXACT_REGION: { icon: Crosshair, label: "Exact region" },
  CELL: { icon: Grid3x3, label: "Table cell" },
  PAGE: { icon: ScanLine, label: "Page" },
  DOCUMENT: { icon: FileText, label: "Document" },
};

/**
 * Where a passage sits in its source document, and how precisely that is known.
 *
 * This is the surface exact PDF highlighting will hang off: an anchor already carries
 * the page, the recorded region, and whether that region may be drawn. Until the viewer
 * can draw it, the same distinction is stated in words, because "page 11" and "this
 * rectangle on page 11" are different provenance claims and should never read alike.
 */
export function SourceAnchorList({ detail }: { detail: EvidenceDetail }) {
  const anchors = sourceAnchors(detail);
  if (!anchors.length) {
    return (
      <p className={styles["anchor-empty"]}>No source location was supplied for this passage.</p>
    );
  }

  return (
    <ul className={styles["anchor-list"]} aria-label="Source locations">
      {anchors.map((anchor) => (
        <li key={anchor.key}>
          <AnchorRow anchor={anchor} />
        </li>
      ))}
    </ul>
  );
}

function AnchorRow({ anchor }: { anchor: SourceAnchor }) {
  const { icon: Icon, label } = PRECISION_COPY[anchor.precision];
  return (
    <div className={styles["anchor-row"]}>
      <span className={`${styles["anchor-precision"]} ${styles[anchor.precision]}`}>
        <Icon size={14} aria-hidden="true" />
        {label}
      </span>
      <span className={styles["anchor-copy"]}>
        <strong>{anchor.label}</strong>
        <small>
          {humanizeCode(anchor.kind)} &middot; {anchor.version.versionLabel} (
          {anchor.version.sourceVersionId})
        </small>
      </span>
      <span className={styles["anchor-highlight"]}>{anchorRegionStatus(anchor)}</span>
    </div>
  );
}

/**
 * The passage itself, or the reason it is not shown.
 *
 * Both surfaces that display a passage route through here so that one licence decision
 * governs both. A restricted record is not a degraded view: the provenance it does carry
 * is complete, and saying so plainly is more useful than an apology for missing text.
 */
export function PassageBody({
  detail,
  policy = renderPolicy(detail),
  variant = "quote",
}: {
  detail: EvidenceDetail;
  policy?: RenderPolicy;
  variant?: "quote" | "document";
}) {
  if (policy.canQuote && detail.exact_text !== null) {
    return variant === "document" ? (
      <div className={styles["source-passage"]}>
        <span>{policy.headline}</span>
        <mark>{detail.exact_text}</mark>
      </div>
    ) : (
      <blockquote className={styles["evidence-quote"]}>
        <Quote size={25} aria-hidden="true" />
        <div>
          <p>{detail.exact_text}</p>
          <cite>
            {detail.publisher_name} &middot; {detail.source_title}
          </cite>
        </div>
      </blockquote>
    );
  }

  return variant === "document" ? (
    <div className={styles["restricted-passage"]}>
      <FileLock2 size={28} aria-hidden="true" />
      <strong>{policy.headline}</strong>
      <span>{policy.explanation}</span>
    </div>
  ) : (
    <div className={styles["licensed-evidence"]}>
      <FileLock2 size={24} aria-hidden="true" />
      <div>
        <strong>{policy.headline}</strong>
        <span>{policy.explanation}</span>
      </div>
    </div>
  );
}
