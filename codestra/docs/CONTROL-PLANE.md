# Codestra control plane

The dependency-free API in `codestra/control_plane/server.py` provides the
versioned `/platform/v1` service catalog and observability registry contract.
It reads `codestra/business-registry.json` and does not modify that source
registry.

Run it locally:

```text
python -m codestra.control_plane.server --host 127.0.0.1 --port 8081
```

This repository keeps the API read-only and validation-only. When `CODESTRA_MIDDLEWARE_URL` and `CODESTRA_MIDDLEWARE_TOKEN_FILE` are configured, catalog, coverage, health, dependency, and integration reads delegate to Middleware; Grafana does not duplicate or persist that state. The registry is
loaded from `codestra/business-registry.json`, and onboarding checks consult the
source-controlled binding manifest in `codestra/onboarding/grafana-bindings.v1.json`.
The API never persists local state or mutates the shared platform registry. The token is sent only server-to-server in the Authorization header and is never returned to callers.

`POST /platform/v1/services/{service_id}/onboard` prepares a local dry-run visualization plan
only. It never activates production traffic, alert delivery, secrets, DNS,
database writes, or remediation. The API intentionally exposes no shell,
arbitrary SQL, raw secret, or production command endpoint.

`GET /platform/v1/observability/integrations` and
`GET /platform/v1/observability/integrations/{integration_id}` delegate to
Middleware's `/v1/observability/integrations[/{integration_id}]`. Note that
`integration_id` (e.g. `backstage`, `sentry`, `wazuh`) is a distinct
identifier from `service_id`; the catalog never conflates the two.

## Staging-only end-to-end verification

Automated unit tests (`tests/test_control_plane.py`) prove this repository's
own logic using a fake Middleware client. They do not prove that a real
network call to a real, migrated, JWT-authenticated Middleware instance
succeeds. For that, use the staging harness:

```text
python tests\e2e\staging_harness.py
```

This harness, with `MIDDLEWARE_REPO_PATH` pointing at a local checkout of
`appolon1908-hue/Middleware-` (default: sibling directory `../Middleware-`):

- Applies Middleware's real Alembic migration `0059_integrated_monitoring`
  against a disposable database (SQLite by default; set
  `MONITORING_TEST_DATABASE_URL` to a disposable `monitoring_test*` Postgres
  database to run against real PostgreSQL).
- Mounts a real `MONITORING_CONFIG_FILE` fixture
  (`codestra/staging/monitoring-config.fixture.json`) registering the
  `middleware` and `odoo` services, matching
  `codestra/onboarding/grafana-bindings.v1.json`.
- Generates a real RSA keypair and serves a real JWKS document over a real
  loopback HTTP server, so Middleware's actual `KeycloakValidator`
  (`app/core/jwt_auth.py`) performs real signature/issuer/audience
  validation of a real RS256 bearer token, not a monkeypatched stub.
- Runs Middleware's actual `app.monitoring.routes` router under a real
  `uvicorn` server bound to a loopback TCP port.
- Exercises this repository's real `MiddlewareClient`
  (`codestra/control_plane/middleware_client.py`) making real HTTP calls
  over that loopback network path, asserting successful reads
  (`repositories`, `service_dependencies`, `overview`, `topology`,
  `service_health`) and correct failure handling for an invalid bearer
  token and an unreachable Middleware host (both must surface as
  `MiddlewareUnavailable`, never a false success).

For a container-based, more production-like variant, `docker-compose.staging.yml`
builds Middleware's own unmodified `Dockerfile` against a real PostgreSQL
container. That compose file is a reviewed reference — it has not been
executed in every environment (it requires a running Docker daemon), so do
not treat it as verified until it has actually been run and its output
captured; `tests/e2e/staging_harness.py` is the currently-proven check and
exercises the identical real Middleware code paths without requiring
Docker. `tests/e2e/serve_jwks.py` provides a standalone synthetic JWKS
issuer usable with the compose file, using the same RSA-keypair pattern.

All of this remains staging-only: no production Middleware/Grafana
credentials, hosts, or databases are touched, and no capability flag
(`LIVE_EMAIL_DELIVERY`, `LIVE_SMS_DELIVERY`, `LIVE_PSTN_DIALING`,
`ODOO_WRITE`, `N8N_WORKFLOWS_ACTIVE`) is enabled.
