import type { Metadata } from "next";
import Link from "next/link";

import { ArmChart } from "@/components/pages/arm-chart";
import chart from "@/components/pages/evaluation.module.css";
import { PowerCurve } from "@/components/pages/power-curve";
import { PageFrame } from "@/components/shell/page-frame";
import styles from "@/components/shell/shell.module.css";
import evaluation from "@/lib/generated/evaluation.json";

/**
 * One headline figure: the value large, its unit small beside it, and anything second on
 * its own line. The page used to state a figure three different ways, once cramming a
 * percentage, the words "95%" and an interval onto one line at three sizes.
 */
function Stat({ value, unit, note }: { value: string; unit?: string; note?: string }) {
  return (
    <span className={chart.stat}>
      <span className={chart.statValue}>
        {value}
        {unit ? <span className={chart.statUnit}>{unit}</span> : null}
      </span>
      {note ? <span className={chart.statNote}>{note}</span> : null}
    </span>
  );
}

export const metadata: Metadata = {
  title: "Evaluation · Sentinel RAG",
  description: "How the instrument was measured: coverage on 164 questions, paired comparisons, and the smallest effect the design can detect.",
};

function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "";
  return `${(value * 100).toFixed(digits)}%`;
}

function interval(value: number[] | null | undefined): string {
  if (!value || value.length !== 2) return "";
  return `${pct(value[0])} to ${pct(value[1])}`;
}

function signed(value: number, digits = 1): string {
  const v = (value * 100).toFixed(digits);
  return value > 0 ? `+${v} points` : `${v} points`;
}

export default function EvaluationPage() {
  const arms = evaluation.arms;
  const paired = evaluation.production_versus_naive;
  const control = evaluation.negative_control_a_versus_b;
  const noise = evaluation.noise_floor;
  const mde = evaluation.mde;
  const overlap = evaluation.retrieval_overlap;
  const production = arms.find((arm) => arm.key === "production_a");
  const powerPoints = Object.entries(mde.power_by_delta).flatMap(([delta, power]) =>
    power === null ? [] : [{ delta: Number(delta), power }],
  );

  return (
    <PageFrame
      title="Evaluation"
      lede="How the instrument was measured, on the released question set, and what the numbers can and cannot say. Every figure here is copied from the released benchmark files, whose digests are listed at the end."
    >
      <section aria-labelledby="evaluation-coverage">
        <h2 id="evaluation-coverage">Coverage on 164 questions</h2>
        <p className={`${styles.prose}`}>
          Four arms answered the same 164 questions with the same seed. The production
          instrument is the pipeline as served; replicate B repeats it to measure run-to-run
          noise; the naive arm retrieves without the verification chain; the closed-book arm
          asks the model alone, with no retrieval.
        </p>
        {production ? (
          <dl className={styles.facts}>
            <div>
              <dt>Answered by production</dt>
              <dd>
                <Stat
                  value={String(production.answered)}
                  note={`of ${production.questions} questions`}
                />
              </dd>
            </div>
            <div>
              <dt>Abstained</dt>
              <dd>
                <Stat value={String(production.abstained)} note="the complement" />
              </dd>
            </div>
            <div>
              <dt>Checks passed</dt>
              <dd>
                <Stat
                  value={pct(production.gate_passed_rate)}
                  note={`95% CI ${interval(production.gate_passed_wilson_95)}`}
                />
              </dd>
            </div>
            <div>
              <dt>Error records</dt>
              <dd>
                <Stat value={String(production.error_records)} note="none recorded" />
              </dd>
            </div>
          </dl>
        ) : null}
        <ArmChart
          caption="Answered rate per arm, of 164 questions. Abstention is the complement."
          summary={`Answered rate by arm: ${arms
            .map((arm) => `${arm.label}, ${pct(arm.answered_rate)}`)
            .join("; ")}.`}
          points={arms.map((arm) => ({
            key: arm.key,
            label: arm.label,
            rate: arm.answered_rate,
            emphasis: arm.key.startsWith("production")
              ? ("accent" as const)
              : ("neutral" as const),
          }))}
        />
        <div className={styles["table-wrap"]}>
          <table className={styles.table} aria-label="Coverage by arm">
            <colgroup>
              <col style={{ width: "28%" }} />
              <col style={{ width: "11%" }} />
              <col style={{ width: "11%" }} />
              <col style={{ width: "13%" }} />
              <col style={{ width: "13%" }} />
              <col style={{ width: "16%" }} />
              <col style={{ width: "8%" }} />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">Arm</th>
                <th scope="col" className={styles.num}>
                  Answered
                </th>
                <th scope="col" className={styles.num}>
                  Abstained
                </th>
                <th scope="col" className={styles.num}>
                  Answered rate (%)
                </th>
                <th scope="col" className={styles.num}>
                  Checks passed (%)
                </th>
                <th scope="col">Checks passed, 95% CI</th>
                <th scope="col" className={styles.num}>
                  Errors
                </th>
              </tr>
            </thead>
            <tbody>
              {arms.map((arm) => (
                <tr key={arm.key}>
                  <td>{arm.label}</td>
                  <td className={styles.num}>{arm.answered}</td>
                  <td className={styles.num}>{arm.abstained}</td>
                  <td className={styles.num}>{pct(arm.answered_rate)}</td>
                  <td className={styles.num}>{pct(arm.gate_passed_rate) || "not gated"}</td>
                  <td>{interval(arm.gate_passed_wilson_95)}</td>
                  <td className={styles.num}>{arm.error_records}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={`${styles.prose} ${styles.muted}`}>
          Checks passed counts questions whose answer, or whose abstention, passed every
          verification gate. The closed-book arm has no gate to pass.
        </p>
      </section>

      <section aria-labelledby="evaluation-paired">
        <h2 id="evaluation-paired">Paired comparisons</h2>
        <p className={styles.prose}>
          The same questions in two arms, compared question by question. The difference in
          answered rate is reported with a Tango score interval and an exact McNemar test on
          the discordant pairs. A replicate pair sets the noise floor first.
        </p>
        <div className={styles["table-wrap"]}>
          <table className={styles.table} aria-label="Paired comparisons">
            <colgroup>
              <col style={{ width: "26%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "12%" }} />
              <col style={{ width: "16%" }} />
              <col style={{ width: "10%" }} />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">Comparison</th>
                <th scope="col" className={styles.num}>
                  Both answered
                </th>
                <th scope="col" className={styles.num}>
                  First only
                </th>
                <th scope="col" className={styles.num}>
                  Second only
                </th>
                <th scope="col" className={styles.num}>
                  Neither
                </th>
                <th scope="col">Difference (points)</th>
                <th scope="col">Tango 95% CI</th>
                <th scope="col" className={styles.num}>
                  McNemar p
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Production A versus replicate B</td>
                <td className={styles.num}>{control.table.both}</td>
                <td className={styles.num}>{control.table.first_only}</td>
                <td className={styles.num}>{control.table.second_only}</td>
                <td className={styles.num}>{control.table.neither}</td>
                <td>{signed(control.difference)}</td>
                <td>{interval(control.tango_95)}</td>
                <td className={styles.num}>{control.mcnemar_exact_p.toFixed(2)}</td>
              </tr>
              <tr>
                <td>Production A versus naive retrieval</td>
                <td className={styles.num}>{paired.table.both}</td>
                <td className={styles.num}>{paired.table.first_only}</td>
                <td className={styles.num}>{paired.table.second_only}</td>
                <td className={styles.num}>{paired.table.neither}</td>
                <td>{signed(paired.difference)}</td>
                <td>{interval(paired.tango_95)}</td>
                <td className={styles.num}>{paired.mcnemar_exact_p.toFixed(2)}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className={`${styles.prose} ${styles.muted}`}>
          Noise floor: across {noise.pairs} replicate pairs, {noise.answered_versus_abstained_disagreement.count} changed
          between answered and abstained (95% upper bound {pct(noise.answered_versus_abstained_disagreement.interval_95[1])}),
          and among the {noise.answered_in_both} answered in both, {noise.claim_count_changed_among_answered_in_both.count} changed
          their claim count (upper bound {pct(noise.claim_count_changed_among_answered_in_both.interval_95[1])}).
        </p>
        <p className={`${styles.prose} ${styles.muted}`}>{paired.reading}</p>
      </section>

      <section aria-labelledby="evaluation-mde">
        <h2 id="evaluation-mde">What the design can detect</h2>
        <p className={styles.prose}>
          With {mde.n} paired questions and a discordance anchor of {mde.anchor.discordant} in{" "}
          {mde.anchor.trials} trials, the smallest difference in answered rate the paired test
          detects with {Math.round(mde.power_target * 100)}% power at alpha {mde.alpha} is{" "}
          {Math.round(mde.mde_at_80_percent * 100)} percentage points. Smaller effects are not
          excluded by a null result here.
        </p>
        <PowerCurve
          caption={`Power against the difference in answered rate, at alpha ${mde.alpha} with ${mde.n} paired questions. The dashed line is the ${Math.round(mde.power_target * 100)} percent target.`}
          summary={`Power rises with the difference in answered rate and first reaches the ${Math.round(mde.power_target * 100)} percent target at ${Math.round(mde.mde_at_80_percent * 100)} percentage points.`}
          marker={{ delta: mde.mde_at_80_percent, power: mde.power_target }}
          points={powerPoints}
          target={mde.power_target}
        />
        <details className={`${styles.details} ${chart.detailsTable}`}>
          <summary>Power at each difference</summary>
          <div className={styles["table-wrap"]}>
            <table className={styles.table} aria-label="Power by difference in answered rate">
              <colgroup>
                <col style={{ width: "50%" }} />
                <col style={{ width: "50%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Difference in answered rate (points)</th>
                  <th scope="col" className={styles.num}>
                    Power (%)
                  </th>
                </tr>
              </thead>
              <tbody>
                {powerPoints.map((point) => (
                  <tr key={point.delta}>
                    <td>{Math.round(point.delta * 100)}</td>
                    <td className={styles.num}>{Math.round(point.power * 100)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <section aria-labelledby="evaluation-retrieval">
        <h2 id="evaluation-retrieval">Retrieval</h2>
        <dl className={styles.facts}>
          <div>
            <dt>Retrieval slots</dt>
            <dd>
              <Stat value={String(overlap.slots)} note="10 per question" />
            </dd>
          </div>
          <div>
            <dt>Distinct passages retrieved</dt>
            <dd>
              <Stat value={String(overlap.distinct_evidence_ids)} note="across every slot" />
            </dd>
          </div>
          <div>
            <dt>Slots holding a recurring passage</dt>
            <dd>
              <Stat value={pct(overlap.recurring_slot_share)} note="of all slots" />
            </dd>
          </div>
          <div>
            <dt>Question pairs sharing a passage</dt>
            <dd>
              <Stat value={pct(overlap.pair_sharing_rate)} note="of all pairs" />
            </dd>
          </div>
        </dl>
        <p className={`${styles.prose} ${styles.muted}`}>
          Between production A and a later run of the same configuration, {evaluation.stage2_versus_a_upper_bound.disagreement.count} of{" "}
          {evaluation.stage2_versus_a_upper_bound.disagreement.total} questions changed outcome
          (95% {interval(evaluation.stage2_versus_a_upper_bound.disagreement.interval_95)}), an upper
          bound that also contains day-to-day drift and a seed difference.
        </p>
      </section>

      <section aria-labelledby="evaluation-sources">
        <h2 id="evaluation-sources">Sources</h2>
        <p className={styles.prose}>
          Statistics generated {evaluation.statistics_generated_at ?? "at release"} with seed{" "}
          {evaluation.seed} and {evaluation.bootstrap_resamples} bootstrap resamples, under Python{" "}
          {evaluation.software?.python}, numpy {evaluation.software?.numpy}, scipy{" "}
          {evaluation.software?.scipy} and statsmodels {evaluation.software?.statsmodels}. The
          pre-registered plan and the analysis code are in the repository; the method is described
          on the <Link href="/methods">Methods</Link> page.
        </p>
        <details className={styles.details}>
          <summary>Released files and digests</summary>
          <dl>
            {Object.entries(evaluation.sources).map(([name, digest]) => (
              <div key={name} style={{ display: "contents" }}>
                <dt>{name}</dt>
                <dd>{digest}</dd>
              </div>
            ))}
          </dl>
        </details>
      </section>
    </PageFrame>
  );
}
