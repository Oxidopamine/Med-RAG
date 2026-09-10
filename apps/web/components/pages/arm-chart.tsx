"use client";

import { useId, useState } from "react";

import styles from "./evaluation.module.css";

export interface ArmPoint {
  key: string;
  label: string;
  /** Fraction, 0 to 1. */
  rate: number;
  /** The production instrument gets the accent; every comparison arm gets a neutral ink. */
  emphasis: "accent" | "neutral";
}

const TICKS = [0, 25, 50, 75, 100];

/*
 * Four arms whose answered rate is nearly identical for three of them: a horizontal dot
 * plot on one shared 0-100% axis, not bars, so the near-equal cluster and the one outlier
 * both read from position rather than from four lengths that are almost the same length.
 *
 * Every row carries its own text label, so colour (production versus comparison arm) is
 * never the only way to tell rows apart. A value is printed beside a dot only where it is
 * not already obvious from the row above it - three identical rates need one label, not
 * three - and the table under the chart is the full data, so nothing here is colour-alone
 * or hover-only.
 */
export function ArmChart({
  points,
  summary,
  caption,
}: {
  points: ArmPoint[];
  summary: string;
  caption: string;
}) {
  const [active, setActive] = useState<string | null>(null);
  const id = useId();

  const width = 640;
  const labelWidth = 208;
  const rightPad = 56;
  const plotWidth = width - labelWidth - rightPad;
  const rowHeight = 40;
  const topPad = 10;
  const axisHeight = 40;
  const plotBottom = topPad + points.length * rowHeight;
  const height = plotBottom + axisHeight;

  const x = (rate: number) => labelWidth + rate * plotWidth;

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

        {TICKS.map((tick) => (
          <line
            key={`grid-${tick}`}
            className={styles.gridline}
            x1={x(tick / 100)}
            x2={x(tick / 100)}
            y1={topPad - 4}
            y2={plotBottom + 4}
          />
        ))}

        <line
          className={styles.axis}
          x1={labelWidth}
          x2={labelWidth + plotWidth}
          y1={plotBottom + 4}
          y2={plotBottom + 4}
        />

        {TICKS.map((tick) => (
          <text
            key={`tick-${tick}`}
            className={styles.tickLabel}
            textAnchor="middle"
            x={x(tick / 100)}
            y={plotBottom + 20}
          >
            {tick}
          </text>
        ))}

        <text
          className={styles.axisLabel}
          textAnchor="middle"
          x={labelWidth + plotWidth / 2}
          y={plotBottom + 36}
        >
          Answered rate (%)
        </text>

        {points.map((point, index) => {
          const rowY = topPad + index * rowHeight + rowHeight / 2;
          const cx = x(point.rate);
          const isActive = active === point.key;
          const previous = points[index - 1];
          const showValue = !previous || Math.abs(previous.rate - point.rate) > 0.001;

          return (
            <g
              key={point.key}
              onBlur={() => setActive(null)}
              onFocus={() => setActive(point.key)}
              onMouseEnter={() => setActive(point.key)}
              onMouseLeave={() => setActive(null)}
              tabIndex={0}
            >
              <rect
                className={styles.hit}
                height={rowHeight}
                width={width}
                x={0}
                y={rowY - rowHeight / 2}
              />
              <text className={styles.rowLabel} x={2} y={rowY + 4}>
                {point.label}
              </text>
              <circle
                className={`${point.emphasis === "accent" ? styles.dotAccent : styles.dotNeutral} ${
                  isActive ? styles.dotActive : ""
                }`}
                cx={cx}
                cy={rowY}
                r={7}
              />
              {showValue ? (
                <text className={styles.dotValue} x={cx + 14} y={rowY + 4}>
                  {(point.rate * 100).toFixed(1)}%
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {active ? (
        <div className={styles.tooltip} role="status">
          {(() => {
            const point = points.find((item) => item.key === active);
            if (!point) return null;
            return `${point.label}: ${(point.rate * 100).toFixed(1)}% answered`;
          })()}
        </div>
      ) : null}
      <figcaption className={styles.caption}>{caption}</figcaption>
    </figure>
  );
}
