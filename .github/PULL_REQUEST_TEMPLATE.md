<!--
Thanks for the PR. Keep this short — a few honest sentences beat a filled-in form that doesn't
match the diff. Delete anything that doesn't apply.
-->

## What this changes

<!-- What does it do, and why? If it closes an issue, link it (e.g. "Closes #12"). -->

## How I tested it

<!-- What did you actually run? e.g. `make test`, a new test, the live stack, a screenshot. -->

## Checklist

- [ ] `make test` passes offline (no Docker, no API keys).
- [ ] `make lint` is clean (ruff 0.15.18, black 26.5.1, mypy 1.11.2) and CI is green.
- [ ] Tests added or updated for the change.
- [ ] If I changed a contract in `libs/edis-contracts`, I updated the Zod mirror in
      `libs/edis-ts-contracts` and regenerated the golden schemas (the drift check passes).
- [ ] If I touched an AI path (L3/L4/L5): every number still comes from computed facts, the
      grounding check holds, and there's a deterministic no-key fallback with a test.

## Anything reviewers should know

<!-- Trade-offs, things you're unsure about, follow-ups you're deliberately leaving out. -->
