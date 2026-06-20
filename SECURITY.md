# Security Policy

## Scope, honestly

EDIS is a reference implementation, not a hardened production deployment, and the [README](README.md)
and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) are explicit about what that means. The MVP ships
a dev-mode JWT with a placeholder secret, application-level tenant filtering (Postgres row-level
security is designed but not yet enforced), an append-only audit log without the tamper-evident hash
chain, and so on. **Those are documented limitations, not vulnerabilities — please don't file them
as security issues.**

What I do want to hear about is a real problem *beyond* those known gaps. For example:

- a way for the copilot's read-only tools to reach another tenant's data,
- a path that lets the model exfiltrate or mutate something it shouldn't,
- an injection or unsafe deserialization at the ingestion edge,
- a real secret committed to the history.

## Supported versions

This is pre-1.0. Only the current `main` branch is supported.

## Reporting

Please report privately — **not** in a public issue:

- **Preferred:** GitHub's private vulnerability reporting (the repo's **Security → Report a
  vulnerability**), if it's enabled.
- **Or** email me at **jaswanth.surya007@gmail.com**.

Include enough detail to reproduce it. I'll acknowledge within a few days and keep you posted on a
fix. This is a solo project, so please allow reasonable time to address an issue before any public
disclosure.
