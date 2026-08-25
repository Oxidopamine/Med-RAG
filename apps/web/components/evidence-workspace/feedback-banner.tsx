import {
  AlertTriangle,
  CheckCircle2,
  CircleAlert,
  Info,
  RotateCw,
  X,
} from "lucide-react";
import type { ReactNode } from "react";

import styles from "./workspace.module.css";

export type FeedbackTone = "error" | "info" | "success" | "warning";

interface FeedbackBannerProps {
  actionLabel?: string;
  message: string;
  onAction?: () => void;
  onDismiss?: () => void;
  title: string;
  tone: FeedbackTone;
}

export function FeedbackBanner({
  actionLabel,
  message,
  onAction,
  onDismiss,
  title,
  tone,
}: FeedbackBannerProps) {
  return (
    <div
      className={`${styles["feedback-banner"]} ${styles[`feedback-${tone}`]}`}
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
    >
      <span className={styles["feedback-icon"]} aria-hidden="true">
        {feedbackIcon(tone)}
      </span>
      <div className={styles["feedback-copy"]}>
        <strong>{title}</strong>
        <span>{message}</span>
      </div>
      {onAction && actionLabel ? (
        <button className={styles["feedback-action"]} type="button" onClick={onAction}>
          <RotateCw size={15} aria-hidden="true" />
          {actionLabel}
        </button>
      ) : null}
      {onDismiss ? (
        <button
          className={styles["feedback-dismiss"]}
          type="button"
          onClick={onDismiss}
          aria-label={`Dismiss ${tone} message`}
        >
          <X size={17} aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

function feedbackIcon(tone: FeedbackTone): ReactNode {
  if (tone === "success") return <CheckCircle2 size={19} />;
  if (tone === "error") return <CircleAlert size={19} />;
  if (tone === "warning") return <AlertTriangle size={19} />;
  return <Info size={19} />;
}
