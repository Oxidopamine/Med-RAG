import type { Metadata } from "next";
import Link from "next/link";

import { PageFrame } from "@/components/shell/page-frame";
import styles from "@/components/shell/shell.module.css";

export const metadata: Metadata = {
  title: "Methods · Sentinel RAG",
  description: "How a review runs, how to read a result, and what the words mean.",
};

export default function MethodsPage() {
  return (
    <PageFrame
      narrow
      title="Methods"
      lede="How a review runs, how to read what comes back, and what each word on the page means. This is the one place the interface explains itself."
    >
      <section aria-labelledby="methods-what">
        <h2 id="methods-what">What this is</h2>
        <div className={styles.prose}>
          <p>
            A research instrument for asking clinical questions of published guidelines. It
            answers only from guideline text it retrieved for the question, and every claim
            carries the page it came from, so the source can be read rather than the summary
            trusted.
          </p>
          <p>
            It serves one approved corpus release at a time. A release is a fixed, signed set
            of guideline editions; the release in use is named in the header and on every
            result. What the release contains is listed on the{" "}
            <Link href="/corpus">Corpus</Link> page.
          </p>
        </div>
      </section>

      <section aria-labelledby="methods-run">
        <h2 id="methods-run">How a review runs</h2>
        <div className={styles.prose}>
          <ol>
            <li>
              <strong>Interpret.</strong> The question is read for its population, condition
              and the decision being asked about. What was read is shown under the question
              and can be corrected before the review runs again.
            </li>
            <li>
              <strong>Retrieve.</strong> Passages are retrieved from the served release, ranked,
              and re-ranked against the question. Retrieval is scoped to the publishers you
              chose; every record in the current release is scoped to the world.
            </li>
            <li>
              <strong>Look for exceptions.</strong> A second search looks for contraindications,
              exceptions and passages that disagree with the first set.
            </li>
            <li>
              <strong>Verify.</strong> Each drafted claim is checked against the passages it
              cites. A claim the text does not carry is dropped, and the count of dropped
              claims is reported with the answer.
            </li>
            <li>
              <strong>Cite or abstain.</strong> An answer arrives with a citation on every claim,
              or it does not arrive at all. An abstention says why, and shows the passages that
              came closest.
            </li>
          </ol>
        </div>
      </section>

      <section aria-labelledby="methods-read">
        <h2 id="methods-read">Reading a result</h2>
        <div className={styles.prose}>
          <p>
            <strong>Claims</strong> are numbered recommendations. Each carries superscript
            citations that open the cited passage in the source pane, and a footnote list under
            the answer gives the full reference for each citation.
          </p>
          <p>
            <strong>Checks</strong> are the five stages above with their outcome. All five pass
            on every answer that is shown; the strip exists so the outcome is visible rather than
            implied.
          </p>
          <p>
            <strong>Sources</strong> shows the cited passage as quoted from the guideline, on
            paper, with its edition, page and effective dates. Where the publisher&apos;s licence
            permits it, the page itself is shown; where it does not, the location is stated and
            the text is not reproduced.
          </p>
          <p>
            <strong>Conflicts</strong> are passages that disagree with each other. Both are shown
            as published, side by side, with the disagreement named. Choosing between them is
            not something the system does.
          </p>
        </div>
      </section>

      <section aria-labelledby="methods-glossary">
        <h2 id="methods-glossary">Glossary</h2>
        <div className={styles.prose}>
          <dl>
            <dt>Release</dt>
            <dd>
              A fixed, signed set of guideline editions with a manifest digest. Every answer
              names the release it was answered from.
            </dd>
            <dt>Activated</dt>
            <dd>
              A release that passed the signed activation decision and is served as approved.
              A release served for research has been validated but not activated, and results
              say so.
            </dd>
            <dt>Supported</dt>
            <dd>A claim whose cited passages carry what it asserts.</dd>
            <dt>Withheld</dt>
            <dd>
              A claim that was drafted and dropped because its passages did not carry it. Only
              the count is shown; the text is not.
            </dd>
            <dt>Not shown (licence)</dt>
            <dd>
              Passage text the publisher&apos;s licence does not permit reproducing. The location,
              edition and page are still stated.
            </dd>
            <dt>No answer</dt>
            <dd>
              The review abstained: nothing retrieved could carry a recommendation, or the
              question could not be answered from the release. The reason and the closest
              passages are shown.
            </dd>
            <dt>Evidence role</dt>
            <dd>
              What a passage does for a claim: primary support, an exception, a dosing
              modifier, or an applicability condition.
            </dd>
          </dl>
        </div>
      </section>

      <section aria-labelledby="methods-boundary">
        <h2 id="methods-boundary">Boundary</h2>
        <div className={styles.prose}>
          <p>
            Research use only. Not authorized for patient care, and not a substitute for the
            guideline or for clinical judgement. Questions are sent to a server and retained with
            the run record, so they must not contain names, identifiers or other protected health
            information; the question field warns when it sees one.
          </p>
          <p>
            How the instrument was measured, and against what, is on the{" "}
            <Link href="/evaluation">Evaluation</Link> page.
          </p>
        </div>
      </section>
    </PageFrame>
  );
}
