import Link from "next/link";

import content from "@/components/pages/content.module.css";
import { PageFrame } from "@/components/shell/page-frame";
import styles from "@/components/shell/shell.module.css";

export default function NotFound() {
  return (
    <PageFrame
      narrow
      title="Nothing at this address"
      lede="The page you asked for does not exist, or the run it named is no longer held by the service."
    >
      <p className={styles.prose}>Check the address, or go to a page that exists.</p>
      <div className={`${styles.actions} ${content["not-found-actions"]}`}>
        <Link className={`${styles.button} ${styles.primary}`} href="/">
          Open the workspace
        </Link>
        <Link className={styles.button} href="/methods">
          Methods
        </Link>
      </div>
    </PageFrame>
  );
}
