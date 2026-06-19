/**
 * Display formatters for the cockpit. Pure functions; numeric values passed in
 * are already authoritative (computed upstream) — formatting never changes a
 * value's meaning, only its presentation.
 */

const COMPACT = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

const USD_COMPACT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Format a metric value with a unit hint (USD / pct / ms / count). */
export function formatMetric(value: number, unit?: string | null): string {
  switch (unit) {
    case "USD":
      return USD_COMPACT.format(value);
    case "pct":
      return `${(value * 100).toFixed(1)}%`;
    case "ms":
      return `${COMPACT.format(value)} ms`;
    case "count":
      return COMPACT.format(value);
    default:
      return COMPACT.format(value);
  }
}

/** Signed percentage delta, e.g. `-8.3%`. `pct` is already a percentage. */
export function formatDeltaPct(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return "—";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

/** Format a raw number for a "grounded fact" chip, with optional unit. */
export function formatFact(
  value: number | null | undefined,
  unit?: string | null,
): string {
  if (value === null || value === undefined) return "—";
  return formatMetric(value, unit);
}
