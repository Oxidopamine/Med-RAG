"use client";

import { useId, useState } from "react";

import styles from "./evaluation.module.css";

export interface ArmBar {
  key: string;
  label: string;
  value: number;
  total: number;
}

/*
 * One measure, four arms: a horizontal bar per arm, one hue, the value written beside
 * the bar. Thin marks on a recessive baseline; hover names the arm and the count. The
 * table under the chart is the same data in full, so nothing is colour-alone.
 */
export function ArmChart({ bars, caption }: { bars: ArmBar[]; caption: string }) {
  const [active, setActive] = useState<string | null>(null);
  const id = useId();
  const max = Math.max(...bars.map((bar) => bar.total), 1);
  const rowHeight = 34;
  const labelWidth = 220;
  const width = 640;
  const plotWidth = width - labelWidth - 72;
  const height = bars.length * rowHeight + 8;

  return (
    <figure className={styles.figure}>
      <svg
        aria-labelledby={`${id}-title`}
        className={styles.chart}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <title id={`${id}-title`}>{caption}</title>
        {bars.map((bar, index) => {
          const y = index * rowHeight + 4;
          const w = Math.max((bar.value / max) * plotWidth, 2);
          const share = bar.total ? Math.round((bar.value / bar.total) * 100) : 0;
          const isActive = active === bar.key;
          return (
            <g
              key={bar.key}
              onBlur={() => setActive(null)}
              onFocus={() => setActive(bar.key)}
              onMouseEnter={() => setActive(bar.key)}
              onMouseLeave={() => setActive(null)}
              tabIndex={0}
            >
              <rect
                className={styles.hit}
                height={rowHeight}
                width={width}
                x={0}
                y={y - 4}
              />
              <text className={styles.rowLabel} x={0} y={y + 17}>
                {bar.label}
              </text>
              <line
                className={styles.baseline}
                x1={labelWidth}
                x2={labelWidth + plotWidth}
                y1={y + rowHeight - 6}
                y2={y + rowHeight - 6}
              />
              <rect
                className={`${styles.bar} ${isActive ? styles.barActive : ""}`}
                height={14}
                rx={0}
                width={w}
                x={labelWidth}
                y={y + 5}
              />
              <rect
                className={`${styles.bar} ${isActive ? styles.barActive : ""}`}
                height={14}
                rx={4}
                width={Math.min(8, w)}
                x={labelWidth + w - Math.min(8, w)}
                y={y + 5}
              />
              <text className={styles.value} x={labelWidth + w + 8} y={y + 17}>
                {bar.value}
                <tspan className={styles.valueMuted}> of {bar.total} ({share}%)</tspan>
              </text>
            </g>
          );
        })}
      </svg>
      {active ? (
        <div className={styles.tooltip} role="status">
          {(() => {
            const bar = bars.find((item) => item.key === active);
            if (!bar) return null;
            return `${bar.label}: ${bar.value} answered of ${bar.total} questions`;
          })()}
        </div>
      ) : null}
      <figcaption className={styles.caption}>{caption}</figcaption>
    </figure>
  );
}
