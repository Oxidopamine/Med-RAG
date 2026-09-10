import type { Metadata } from "next";
import Link from "next/link";

import { PageFrame } from "@/components/shell/page-frame";
import styles from "@/components/shell/shell.module.css";
import { AUTHOR } from "@/lib/author";

import packageJson from "../../package.json";

export const metadata: Metadata = {
  title: "About · Sentinel RAG",
  description: "What Sentinel RAG is, who built it, and under which licences it reads.",
};

export default function AboutPage() {
  return (
    <PageFrame
      narrow
      title="About"
      lede="A research instrument for evidence-gated review of clinical guidelines."
    >
      <section aria-labelledby="about-product" className={styles.card}>
        <h2 id="about-product">The instrument</h2>
        <div className={styles.prose}>
          <p>
            Sentinel RAG answers clinical questions from published guidelines and shows its
            evidence. It retrieves passages from an approved corpus release, verifies every
            claim it drafts against those passages, and either cites each claim to a page or
            abstains. The method is described on the <Link href="/methods">Methods</Link> page
            and its measurement on the <Link href="/evaluation">Evaluation</Link> page.
          </p>
        </div>
        <dl className={styles.facts}>
          <div>
            <dt>Version</dt>
            <dd>{packageJson.version}</dd>
          </div>
          <div>
            <dt>Status</dt>
            {/* A sentence, not a figure: it takes the status mark rather than the size
                the band gives a number. */}
            <dd>
              <span className={`${styles.status} ${styles.warn}`}>Research prototype</span>
              <span className={styles["fact-note"]}>Not authorized for patient care.</span>
            </dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="about-licences" className={styles.card} id="licences">
        <h2 id="about-licences">Licences</h2>
        <div className={styles.prose}>
          <p>
            Guideline text is reproduced under each publisher&apos;s terms. For the World Health
            Organization releases served today, passages may be quoted with their location, and
            page images are withheld. Where a licence permits neither, the location is stated
            and the passage is not shown. The licence terms recorded for each document are
            listed on the <Link href="/corpus">Corpus</Link> page.
          </p>
          <p>
            Questions are sent to the evidence service and retained with the run record. Do not
            enter names, identifiers or other protected health information.
          </p>
        </div>
      </section>

      <section aria-labelledby="about-author" className={styles.card}>
        <h2 id="about-author">Author</h2>
        <div className={styles.prose}>
          <dl>
            <dt>Built by</dt>
            <dd>{AUTHOR.name}</dd>
            {AUTHOR.github || AUTHOR.linkedin ? (
              <>
                <dt>Profiles</dt>
                <dd>
                  {AUTHOR.github ? (
                    <a href={AUTHOR.github} rel="noreferrer noopener" target="_blank">
                      GitHub
                    </a>
                  ) : null}
                  {AUTHOR.github && AUTHOR.linkedin ? " · " : null}
                  {AUTHOR.linkedin ? (
                    <a href={AUTHOR.linkedin} rel="noreferrer noopener" target="_blank">
                      LinkedIn
                    </a>
                  ) : null}
                </dd>
              </>
            ) : null}
          </dl>
        </div>
      </section>
    </PageFrame>
  );
}
