/**
 * Forecast schemas. Re-exports the shared `Forecast` payload
 * (`edis.forecasts.v1`). The shared schema types `points` loosely
 * (`Record<string, unknown>[]`) because the Python contract uses
 * `list[dict]`; here we add a STRICT point shape for the chart (ts/yhat/bands)
 * derived from §4.3, validated separately so a malformed band point is caught at
 * the chart boundary without rejecting the whole forecast envelope.
 */
import { z } from "zod";
import { ForecastSchema, type Forecast } from "@edis/contracts";

export { ForecastSchema };
export type { Forecast };

/** `GET /v1/forecasts` response. */
export const ForecastListSchema = z.array(ForecastSchema);
export type ForecastList = z.infer<typeof ForecastListSchema>;

/**
 * Strict forecast band point — the documented `{ts, yhat, yhat_lower,
 * yhat_upper}` shape. Use `parseForecastPoints` to coerce the loosely-typed
 * `Forecast.points` into chartable rows.
 */
export const ForecastPointSchema = z.object({
  ts: z.string().datetime({ offset: true }),
  yhat: z.number(),
  yhat_lower: z.number(),
  yhat_upper: z.number(),
});
export type ForecastPoint = z.infer<typeof ForecastPointSchema>;

export const ForecastPointsSchema = z.array(ForecastPointSchema);

/** Validate the band points off a `Forecast`; returns [] on shape mismatch. */
export function parseForecastPoints(forecast: Forecast): ForecastPoint[] {
  const result = ForecastPointsSchema.safeParse(forecast.points);
  return result.success ? result.data : [];
}
