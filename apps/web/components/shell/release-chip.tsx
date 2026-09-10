"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

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

export function ReleaseChip({
  prefix = "Release",
  href,
}: {
  prefix?: string;
  href?: string;
}) {
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

  /** The chip is a link wherever it can lead somewhere: the release is a page. */
  const chip = (tone: string, body: ReactNode, title?: string) => {
    const className = `${styles.release} ${tone}`.trim();
    return href ? (
      <Link className={className} href={href} title={title}>
        {body}
      </Link>
    ) : (
      <span className={className} title={title}>
        {body}
      </span>
    );
  };

  if (state === "checking") return chip("", "Checking release");
  if (state === "unreachable") return chip(styles.none, "Evidence service not reached");
  if (!state.corpus_release_id) return chip(styles.none, "No active release");

  const tone = state.serving_mode === "ACTIVATED" ? styles.approved : styles.research;
  return chip(
    tone,
    <>
      {prefix} <strong>{state.corpus_release_id}</strong>
      {state.serving_mode === "RESEARCH_UNACTIVATED" ? " (research)" : ""}
    </>,
    state.corpus_release_id,
  );
}
