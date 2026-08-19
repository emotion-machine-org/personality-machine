# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via [GitHub Security Advisories](../../security/advisories/new) rather than public issues. We aim to acknowledge reports within a few business days.

## Supported versions

Only the latest `main` branch is supported.

## Development-mode flags

Two environment flags intentionally weaken security for local development. Both default to **off** and must never be enabled in production:

- `DISABLE_AUTH_FOR_TESTING=true` — disables all authentication and exposes an unauthenticated `GET /api/auth/dev-token` endpoint that mints a valid dev token.
- `SEED_ENDPOINT_ENABLED=true` — enables data-seeding endpoints.

If you find a way to bypass authentication **without** these flags, that is a vulnerability — please report it.
