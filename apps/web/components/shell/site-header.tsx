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
            <ReleaseChip />
            <Link
              className={styles["settings-link"]}
              href="/settings"
              aria-current={isCurrent(pathname, "/settings") ? "page" : undefined}
            >
              Settings
            </Link>
          </div>
        </div>
        <details className={styles.drawer}>
          <summary>Menu</summary>
          <nav aria-label="Primary, phone">
            {links}
            <Link href="/settings">Settings</Link>
            <Link href="/about">About</Link>
          </nav>
        </details>
      </header>
    </>
  );
}
