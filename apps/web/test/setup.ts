/**
 * Vitest setup — jsdom + Testing Library matchers + an MSW server that runs with
 * NO backend (B3 registers handlers per-test). The SSE client uses `fetch` with
 * a streamed body, so component tests can drive realtime via a mocked
 * `ReadableStream` without a real server.
 */
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { setupServer } from "msw/node";
import type { RequestHandler } from "msw";

/** Empty by default; tests add handlers via `server.use(...)`. */
const handlers: RequestHandler[] = [];

export const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
