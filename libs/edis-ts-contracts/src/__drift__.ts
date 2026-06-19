/**
 * Zod-vs-Pydantic drift check (run in CI: `npm run drift`).
 *
 * The Python contracts are the single source of truth. Part A of F6 commits a
 * JSON-Schema golden for every model to
 * `libs/edis-contracts/tests/golden/<Model>.json`. This script loads those
 * goldens and asserts the hand-written Zod schemas in this package stay in lockstep:
 *
 *   1. Field PARITY -- the set of property names in each Zod schema equals the set
 *      in the Python golden (catches a field added on one side only).
 *   2. REQUIREDNESS -- a field required in Python (no default) is required in Zod,
 *      and vice-versa (catches an optionality flip).
 *   3. `schema_version` TYPE -- it is an integer in the golden AND a Zod literal
 *      `1` here (the exact int-vs-str drift §4.3 eliminates).
 *
 * This is intentionally a STRUCTURAL check (names + requiredness + the
 * schema_version type), not a full type-by-type JSON-Schema equivalence: it is
 * cheap, high-signal, and stable across pydantic/zod patch upgrades. A deeper
 * generator-based equivalence is a documented future upgrade; the seam (goldens +
 * this script) already exists.
 *
 * Exit code is non-zero on any drift so CI fails. Requires no network and no
 * Python runtime -- it reads the committed goldens only.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";

import { IngestEnvelopeSchema } from "./ingest.js";
import { CanonicalEventSchema, MetricPointSchema } from "./canonical.js";
import { FindingSchema, ForecastSchema } from "./findings.js";
import {
  OutcomeReportSchema,
  RecommendationLifecycleEventSchema,
  RecommendationSchema,
} from "./decisions.js";
import {
  AuditEventSchema,
  DecisionSchema,
  LineageEventSchema,
} from "./governance.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const GOLDEN_DIR = join(
  __dirname,
  "..",
  "..",
  "edis-contracts",
  "tests",
  "golden",
);

/** Minimal JSON-Schema shape we read out of the Python goldens. */
interface JsonSchema {
  properties?: Record<string, { type?: string; default?: unknown }>;
  required?: string[];
}

/**
 * The key payload models F6 covers, mapped Zod schema <-> golden file name. The
 * golden filename is the Python class name (Part A writes `<ClassName>.json`).
 */
const COVERED: ReadonlyArray<{
  name: string;
  golden: string;
  schema: z.ZodTypeAny;
}> = [
  { name: "IngestEnvelope", golden: "IngestEnvelope", schema: IngestEnvelopeSchema },
  { name: "CanonicalEvent", golden: "CanonicalEvent", schema: CanonicalEventSchema },
  { name: "MetricPoint", golden: "MetricPoint", schema: MetricPointSchema },
  { name: "Finding", golden: "Finding", schema: FindingSchema },
  { name: "Forecast", golden: "Forecast", schema: ForecastSchema },
  { name: "Recommendation", golden: "Recommendation", schema: RecommendationSchema },
  {
    name: "RecommendationLifecycleEvent",
    golden: "RecommendationLifecycleEvent",
    schema: RecommendationLifecycleEventSchema,
  },
  { name: "OutcomeReport", golden: "OutcomeReport", schema: OutcomeReportSchema },
  { name: "AuditEvent", golden: "AuditEvent", schema: AuditEventSchema },
  { name: "LineageEvent", golden: "LineageEvent", schema: LineageEventSchema },
  { name: "Decision", golden: "Decision", schema: DecisionSchema },
];

function loadGolden(file: string): JsonSchema {
  const path = join(GOLDEN_DIR, `${file}.json`);
  return JSON.parse(readFileSync(path, "utf-8")) as JsonSchema;
}

/** Field names + which are required, derived from a Python JSON-Schema golden. */
function pyFields(schema: JsonSchema): { fields: Set<string>; required: Set<string> } {
  return {
    fields: new Set(Object.keys(schema.properties ?? {})),
    required: new Set(schema.required ?? []),
  };
}

/**
 * Field names + which are required, derived from a Zod object schema.
 * A Zod field is "required" iff it is neither optional, nullable-with-default,
 * nor has a default -- mirroring "no default in Pydantic => required".
 */
function zodFields(schema: z.ZodTypeAny): { fields: Set<string>; required: Set<string> } {
  const obj = unwrapObject(schema);
  if (!obj) {
    throw new Error("expected a ZodObject at the top level");
  }
  const shape = obj.shape as Record<string, z.ZodTypeAny>;
  const fields = new Set(Object.keys(shape));
  const required = new Set<string>();
  for (const [key, value] of Object.entries(shape)) {
    if (isRequired(value)) required.add(key);
  }
  return { fields, required };
}

function unwrapObject(schema: z.ZodTypeAny): z.ZodObject<z.ZodRawShape> | null {
  let cur: z.ZodTypeAny = schema;
  // Peel effects/defaults/optional/nullable wrappers off the top.
  // eslint-disable-next-line no-constant-condition
  while (true) {
    if (cur instanceof z.ZodObject) return cur as z.ZodObject<z.ZodRawShape>;
    const def = (cur as { _def?: { innerType?: z.ZodTypeAny; schema?: z.ZodTypeAny } })
      ._def;
    if (def?.innerType) cur = def.innerType;
    else if (def?.schema) cur = def.schema;
    else return null;
  }
}

/** A Zod field is required if it has no default and is not optional. */
function isRequired(field: z.ZodTypeAny): boolean {
  if (field instanceof z.ZodDefault) return false;
  if (field instanceof z.ZodOptional) return false;
  // `.nullish()` => ZodOptional wrapping ZodNullable; caught above.
  return true;
}

/** Assert `schema_version` is an integer in Python and a literal `1` in Zod. */
function checkSchemaVersion(
  name: string,
  py: JsonSchema,
  zod: z.ZodTypeAny,
  errors: string[],
): void {
  const pyProp = py.properties?.schema_version;
  if (!pyProp) return; // model has no schema_version (e.g. supporting types)
  if (pyProp.type !== "integer") {
    errors.push(
      `${name}: python schema_version type is '${pyProp.type}', expected 'integer' (int-vs-str drift)`,
    );
  }
  const obj = unwrapObject(zod);
  const field = obj?.shape["schema_version"] as z.ZodTypeAny | undefined;
  // Peel the ZodDefault to reach the literal.
  const inner = field instanceof z.ZodDefault ? field._def.innerType : field;
  if (!(inner instanceof z.ZodLiteral) || inner.value !== 1) {
    errors.push(
      `${name}: zod schema_version must be z.literal(1).default(1) to mirror the integer-1 contract`,
    );
  }
}

function diff(a: Set<string>, b: Set<string>): string[] {
  return [...a].filter((x) => !b.has(x)).sort();
}

function main(): void {
  const errors: string[] = [];

  for (const { name, golden, schema } of COVERED) {
    let py: JsonSchema;
    try {
      py = loadGolden(golden);
    } catch (e) {
      errors.push(
        `${name}: could not load golden ${golden}.json -- did Part A bootstrap & commit tests/golden/? (${(e as Error).message})`,
      );
      continue;
    }

    const p = pyFields(py);
    const z = zodFields(schema);

    const onlyPy = diff(p.fields, z.fields);
    const onlyZod = diff(z.fields, p.fields);
    if (onlyPy.length) errors.push(`${name}: fields in Pydantic but missing in Zod: ${onlyPy.join(", ")}`);
    if (onlyZod.length) errors.push(`${name}: fields in Zod but missing in Pydantic: ${onlyZod.join(", ")}`);

    // Requiredness parity, only for fields present on both sides.
    for (const f of p.fields) {
      if (!z.fields.has(f)) continue;
      const pyReq = p.required.has(f);
      const zReq = z.required.has(f);
      if (pyReq !== zReq) {
        errors.push(
          `${name}.${f}: required mismatch -- Pydantic ${pyReq ? "required" : "optional"} vs Zod ${zReq ? "required" : "optional"}`,
        );
      }
    }

    checkSchemaVersion(name, py, schema, errors);
  }

  if (errors.length) {
    console.error("Zod-vs-Pydantic contract DRIFT detected:\n");
    for (const e of errors) console.error("  - " + e);
    console.error(
      `\n${errors.length} drift error(s). Fix the Zod schema (or regenerate the Python golden with EDIS_UPDATE_GOLDEN=1 if the contract change is intentional).`,
    );
    process.exit(1);
  }

  console.log(`OK: ${COVERED.length} payload schemas match their Pydantic goldens (fields + requiredness + schema_version).`);
}

main();
