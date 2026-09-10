"use client";

import { useId } from "react";

import styles from "./evaluation.module.css";

export interface PowerPoint {
  /** Difference in answered rate, as a fraction (0.08 is 8 percentage points). */
  delta: number;
  /** Statistical power, 0 to 1. */
  power: number;
}

const X_TICKS = [0, 0.02, 0.04, 0.06, 0.08, 0.1];
const Y_TICKS = [0, 0.2, 0.4, 0.6, 0.8, 1];
const MAX_DELTA = 0.1;

/*
 * The line chart the "what the design can detect" table was standing in for: difference
 * in answered rate on x, power on y, a dashed reference at the pre-registered 80% power
 * target, and a marked point at the smallest tested difference that reaches it. The table
 * behind `details` on the page carries the same values in full - this is the picture of
 * them, not a replacement for them.
 */
export function PowerCurve({
  points,
  target,
  marker,
  summary,
  caption,
}: {
  points: PowerPoint[];
  target: number;
  marker: PowerPoint;
  summary: string;
  caption: string;
}) {
  const id = useId();
  const width = 640;
  const height = 300;
  const marginLeft = 46;
  const marginRight = 20;
  const marginTop = 16;
  const marginBottom = 46;
  const plotWidth = width - marginLeft - marginRight;
  const plotHeight = height - marginTop - marginBottom;

  const x = (delta: number) => marginLeft + (delta / MAX_DELTA) * plotWidth;
  const y = (power: number) => marginTop + (1 - power) * plotHeight;

  const sorted = [...points].sort((a, b) => a.delta - b.delta);
  const path = sorted
    .map((point, index) => `${index === 0 ? "M" : "L"}${x(point.delta).toFixed(2)} ${y(point.power).toFixed(2)}`)
    .join(" ");

  return (
    <figure className={styles.figure}>
      <svg
        aria-label={summary}
        className={styles.chart}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <title id={`${id}-title`}>{caption}</title>

        {Y_TICKS.map((tick) => (
          <line
            key={`gy-${tick}`}
            className={styles.gridline}
            x1={marginLeft}
            x2={width - marginRight}
            y1={y(tick)}
            y2={y(tick)}
          />
        ))}
        {X_TICKS.map((tick) => (
          <line
            key={`gx-${tick}`}
            className={styles.gridline}
            x1={x(tick)}
            x2={x(tick)}
            y1={marginTop}
            y2={height - marginBottom}
          />
        ))}

        <line
          className={styles.reference}
          x1={marginLeft}
          x2={width - marginRight}
          y1={y(target)}
          y2={y(target)}
        />
        {/* At the left end of the line. Against the right end it sat on top of the
            marker's own label, which names the same crossing point. */}
        <text className={styles.referenceLabel} textAnchor="start" x={marginLeft + 6} y={y(target) - 6}>
          {Math.round(target * 100)}% power target
        </text>

        <line className={styles.axis} x1={marginLeft} x2={marginLeft} y1={marginTop} y2={height - marginBottom} />
        <line
          className={styles.axis}
          x1={marginLeft}
          x2={width - marginRight}
          y1={height - marginBottom}
          y2={height - marginBottom}
        />

        {X_TICKS.map((tick) => (
          <text
            key={`xt-${tick}`}
            className={styles.tickLabel}
            textAnchor="middle"
            x={x(tick)}
            y={height - marginBottom + 18}
          >
            {Math.round(tick * 100)}
          </text>
        ))}
        {Y_TICKS.map((tick) => (
          <text key={`yt-${tick}`} className={styles.tickLabel} textAnchor="end" x={marginLeft - 8} y={y(tick) + 4}>
            {Math.round(tick * 100)}
          </text>
        ))}

        <text className={styles.axisLabel} textAnchor="middle" x={marginLeft + plotWidth / 2} y={height - 6}>
          Difference in answered rate (percentage points)
        </text>
        <text
          className={styles.axisLabel}
          textAnchor="middle"
          transform={`translate(14 ${marginTop + plotHeight / 2}) rotate(-90)`}
        >
          Power (%)
        </text>

        <path className={styles.line} d={path} />
        {sorted.map((point) => (
          <circle key={point.delta} className={styles.point} cx={x(point.delta)} cy={y(point.power)} r={7} />
        ))}

        <circle className={styles.marker} cx={x(marker.delta)} cy={y(marker.power)} r={8} />
        <text className={styles.markerLabel} x={x(marker.delta) + 12} y={y(marker.power) - 10}>
          {Math.round(marker.delta * 100)} pts at {Math.round(marker.power * 100)}%
        </text>
      </svg>
      <figcaption className={styles.caption}>{caption}</figcaption>
    </figure>
  );
}
