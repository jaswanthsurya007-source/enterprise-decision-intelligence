# Contributing

Thanks for looking. A bit of context up front, because it sets expectations: EDIS is a reference
implementation and portfolio project, not a product with a community roadmap. I'm not expecting
large contributions — but bug reports, small fixes, clearer docs, and sharp questions are all
genuinely welcome.

If you're thinking about something bigger than a small fix, **open an issue first**. The project is
a deliberate vertical slice (see *"What's built, what isn't"* in the README), so it's worth a quick
conversation before either of us spends time on something that doesn't fit the scope.

## Getting set up

Everything you need is in the [README](README.md). The short version:

```bash
make install   # libs + services, editable, in dependency order
make test      # the full python suite — no Docker, no API keys
```

You don't need Docker or any API keys to develop or run the tests. The whole vertical slice runs in
process. You only need Docker for the live stack (`make up`) and the integration tests, and keys
only to see the real Claude / Voyage paths instead of the deterministic fallbacks.

## What'll get a change merged

These aren't bureaucracy — they're the invariants the project is built on, and CI enforces most of
them:

- **The suite stays green offline.** `make test` has to pass with no Docker and no keys. Anything
  that needs Postgres, Redpanda, or Redis goes behind `@pytest.mark.integration`.
- **Match the tool versions so CI agrees with you.** ruff 0.15.18, black 26.5.1, mypy 1.11.2,
  Python 3.12. Run `make lint` (and `make fmt`) before you push. CI runs ruff, black, mypy, the
  suite, and a Python↔TypeScript contract drift check on every push.
- **Contracts are the single source of truth.** If you change a payload in `libs/edis-contracts`,
  update the Zod mirror in `libs/edis-ts-contracts` (the drift check will catch you otherwise) and
  regenerate the golden schemas.
- **Keep the monorepo rule.** A package's `dependencies` are third-party only — sibling libs are
  installed editable, never listed as PyPI deps.
- **The one non-negotiable: the model never produces a number.** If you touch the intelligence,
  decision, or copilot layers, every figure has to come from computed facts and the grounding check
  has to still hold. Any new AI path needs a deterministic, no-key fallback *and* a test for it.
- **Add or update tests** for whatever you change.

## Commits and pull requests

- Keep commits small and focused, with a message that says *what* and *why*.
- Open the PR against `main`, fill in the template, and make sure CI is green.

If anything here is unclear, opening an issue is the fastest way to ask.
