/**
 * Shared Zod primitives mirroring the conventions in `edis-contracts` (Pydantic).
 *
 * These centralize the type mappings so every schema file is consistent and the
 * Zod-vs-Pydantic drift check (`src/__drift__.ts`) compares against one set of
 * rules:
 *
 *  - Pydantic `UUID`                -> {@link uuid}            (`z.string().uuid()`)
 *  - Pydantic tz-aware `datetime`   -> {@link datetime}       (ISO-8601 w/ offset)
 *  - Pydantic `dict[str, str]`      -> {@link strMap}
 *  - Pydantic `dict[str, Any]`      -> {@link anyMap}
 *  - Pydantic `Decimal`            -> {@link decimal}        (JSON: number | string)
 *  - Pydantic `schema_version:int=1`-> {@link schemaVersion}  (`z.literal(1)`)
 */
import { z } from "zod";

/** Pydantic `UUID` serializes to a string; validate the canonical form. */
export const uuid = () => z.string().uuid();

/**
 * Pydantic tz-aware `datetime` -> ISO-8601 string with a UTC offset.
 * `offset: true` accepts `...Z` and `...+00:00`; the platform always emits UTC.
 */
export const datetime = () => z.string().datetime({ offset: true });

/** Pydantic `dict[str, str]` (e.g. `dimensions`, `props`). */
export const strMap = () => z.record(z.string(), z.string());

/** Pydantic `dict[str, Any]` / open `dict` (e.g. `payload`, `actor`, `resource`). */
export const anyMap = () => z.record(z.string(), z.unknown());

/** Pydantic open `dict` whose values are floats (e.g. `inputs`, `components`). */
export const floatMap = () => z.record(z.string(), z.number());

/**
 * Pydantic `Decimal`. In JSON Schema pydantic renders this as `number | string`
 * (a string preserves precision on the wire); accept both on the TS boundary.
 */
export const decimal = () => z.union([z.number(), z.string()]);

/**
 * `schema_version: int = 1` -- locked to the literal `1` so a producer on a
 * different contract version is rejected at the boundary (the whole point of the
 * uniform integer `schema_version`, §4.3). Defaults to 1 when omitted.
 */
export const schemaVersion = () => z.literal(1).default(1);

/**
 * Generic event-version field for forward-compat readers that only need "some
 * integer version" rather than a pinned literal. Not used by the payloads (they
 * pin `schemaVersion()`), but documents the looser `z.number().int()` form the
 * F6 unit allows.
 */
export const schemaVersionInt = () => z.number().int().default(1);
