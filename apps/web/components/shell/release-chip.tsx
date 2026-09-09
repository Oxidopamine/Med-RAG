"use client";

import { useEffect, useState } from "react";

import { getCorpusReadiness } from "@/lib/api";
import { withRetries } from "@/lib/readiness";
import type { CorpusReadiness } from "@/lib/types";

import styles from "./shell.module.css";

/*
 * One readiness request for the whole page, shared by the header and the footer. The
 * workspace runs its own with retries and focus rechecks; this one only names the
 * release in the chrome, so a miss here is quiet.
 */
let shared: Promise<CorpusReadiness> | null = null;

function readiness(): Promise<CorpusReadiness> {
  if (!shared) {
    shared = withRetries(getCorpusReadiness, { delays: [1_000, 3_000] }).catch((error) => {
      shared = null;
      throw error;
    });
  }
  return shared;
}

export function ReleaseChip({ prefix = "Release" }: { prefix?: string }) {
  const [state, setState] = useState<CorpusReadiness | "checking" | "unreachable">("checking");

  useEffect(() => {
    let live = true;
    readiness()
      .then((value) => {
        if (live) setState(value);
      })
      .catch(() => {
        if (live) setState("unreachable");
      });
    return () => {
      live = false;
    };
  }, []);

  if (state === "checking") {
    return <span className={styles.release}>Checking release</span>;
  }
  if (state === "unreachable") {
    return <span className={`${styles.release} ${styles.none}`}>Evidence service not reached</span>;
  }
  if (!state.corpus_release_id) {
    return <span className={`${styles.release} ${styles.none}`}>No active release</span>;
  }
  const tone = state.serving_mode === "ACTIVATED" ? styles.approved : styles.research;
  return (
    <span className={`${styles.release} ${tone}`} title={state.corpus_release_id}>
      {prefix} <strong>{state.corpus_release_id}</strong>
      {state.serving_mode === "RESEARCH_UNACTIVATED" ? " (research)" : ""}
    </span>
  );
}
