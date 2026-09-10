"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { BrandMark } from "@/components/evidence-workspace/brand-mark";

import { ReleaseChip } from "./release-chip";
import styles from "./shell.module.css";

export const NAVIGATION = [
  { href: "/", label: "Workspace" },
  { href: "/corpus", label: "Corpus" },
  { href: "/reviews", label: "Reviews" },
  { href: "/methods", label: "Methods" },
  { href: "/evaluation", label: "Evaluation" },
  { href: "/labelling", label: "Labelling" },
] as const;

function isCurrent(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/" || pathname.startsWith("/r/");
  return pathname === href || pathname.startsWith(`${href}/`);
}

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
    </svg>
  );
}

export function SiteHeader() {
  const pathname = usePathname() ?? "/";
  const links = NAVIGATION.map((item) => (
    <Link
      aria-current={isCurrent(pathname, item.href) ? "page" : undefined}
      href={item.href}
      key={item.href}
    >
      {item.label}
    </Link>
  ));

  return (
    <>
      <a className={styles["skip-link"]} href="#main-content">
        Skip to main content
      </a>
      <header className={styles.header}>
        <div className={styles["header-inner"]}>
          {/* The phone menu opens from inside the bar. It used to sit on the page below
              the bar, where it read as a stray link rather than as the navigation. */}
          <details className={styles.drawer}>
            <summary aria-label="Menu" />
            <nav aria-label="Primary, phone">
              {links}
              <Link href="/settings">Settings</Link>
              <Link href="/about">About</Link>
            </nav>
          </details>
          <Link className={styles.brand} href="/" aria-label="Sentinel RAG home">
            <span className={styles["brand-mark"]} aria-hidden="true">
              <BrandMark />
            </span>
            <span className={styles["brand-name"]}>Sentinel RAG</span>
          </Link>
          <nav className={styles.nav} aria-label="Primary">
            {links}
          </nav>
          <div className={styles["header-side"]}>
            <ReleaseChip href="/corpus" />
            <Link
              className={styles["settings-link"]}
              href="/settings"
              aria-current={isCurrent(pathname, "/settings") ? "page" : undefined}
            >
              <GearIcon />
              Settings
            </Link>
          </div>
        </div>
      </header>
    </>
  );
}
