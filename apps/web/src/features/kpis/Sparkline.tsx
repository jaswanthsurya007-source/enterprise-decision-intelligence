/**
 * Sparkline — a compact, axis-less trend line for a KPI tile. Pure SVG (no
 * Recharts overhead for a 1-line micro-chart). Samples are server-rolled-up
 * `{ts, value}` points; the component only draws them, never aggregates.
 */
import { useMemo } from "react";
import type { KpiSparkPoint } from "../../schemas/kpi";

export interface SparklineProps {
  points: KpiSparkPoint[];
  width?: number;
  height?: number;
  /** Tailwind stroke color class via currentColor; set on the parent. */
  className?: string;
}

export function Sparkline({
  points,
  width = 120,
  height = 32,
  className,
}: SparklineProps) {
  const path = useMemo(() => {
    if (points.length < 2) return null;
    const values = points.map((p) => p.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = width / (points.length - 1);
    const pad = 2;
    const usableH = height - pad * 2;
    return points
      .map((p, i) => {
        const x = i * stepX;
        const y = pad + usableH - ((p.value - min) / span) * usableH;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }, [points, width, height]);

  if (!path) {
    return (
      <div
        className="text-2xs text-fg-subtle"
        style={{ width, height }}
        aria-hidden
      />
    );
  }

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      role="img"
      aria-label="trend sparkline"
      preserveAspectRatio="none"
    >
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
