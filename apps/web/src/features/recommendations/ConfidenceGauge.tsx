/**
 * ConfidenceGauge — a compact radial gauge for a recommendation's blended
 * confidence (0..1, computed by L4; never the LLM). The arc fills proportionally
 * and is tinted by band (low/med/high). The component renders the upstream value
 * verbatim — it does not derive confidence.
 */
export interface ConfidenceGaugeProps {
  /** 0..1 blended confidence. */
  value: number;
  size?: number;
}

function tone(value: number): { stroke: string; text: string } {
  if (value >= 0.75) return { stroke: "rgb(var(--status-ok))", text: "text-status-ok" };
  if (value >= 0.5)
    return { stroke: "rgb(var(--status-warn))", text: "text-status-warn" };
  return { stroke: "rgb(var(--status-critical))", text: "text-status-critical" };
}

export function ConfidenceGauge({ value, size = 84 }: ConfidenceGaugeProps) {
  const clamped = Math.min(1, Math.max(0, value));
  const stroke = 8;
  const r = (size - stroke) / 2;
  const cx = size / 2;
  const cy = size / 2;
  // 270° sweep (gauge style), starting at 135°.
  const sweep = 0.75;
  const circumference = 2 * Math.PI * r;
  const arcLen = circumference * sweep;
  const dash = arcLen * clamped;
  const { stroke: strokeColor, text } = tone(clamped);

  return (
    <div
      className="relative inline-flex items-center justify-center"
      style={{ width: size, height: size }}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={clamped}
      data-testid="confidence-gauge"
    >
      <svg width={size} height={size} className="-rotate-[135deg]">
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke="rgb(var(--surface-overlay))"
          strokeWidth={stroke}
          strokeDasharray={`${arcLen} ${circumference}`}
          strokeLinecap="round"
        />
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke={strokeColor}
          strokeWidth={stroke}
          strokeDasharray={`${dash} ${circumference}`}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={`kpi-figure text-lg ${text}`}>
          {clamped.toFixed(2)}
        </span>
        <span className="text-2xs uppercase tracking-wide text-fg-subtle">
          conf
        </span>
      </div>
    </div>
  );
}
