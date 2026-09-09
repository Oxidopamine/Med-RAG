import Link from "next/link";

import { PageFrame } from "@/components/shell/page-frame";
import styles from "@/components/shell/shell.module.css";

export default function NotFound() {
  return (
    <PageFrame narrow title="Nothing at this address" lede="The page you asked for does not exist, or the run it named is no longer held by the service.">
      <div className={styles.actions}>
        <Link className={`${styles.button} ${styles.primary}`} href="/">
          Open the workspace
        </Link>
        <Link className={styles.button} href="/reviews">
          Reviews
        </Link>
        <Link className={styles.button} href="/corpus">
          Corpus
        </Link>
      </div>
    </PageFrame>
  );
}
