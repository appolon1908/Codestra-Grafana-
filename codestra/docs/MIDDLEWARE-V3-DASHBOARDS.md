# Middleware V3 dashboards and datasources (Lane E preparation)

Status: **PREPARED_DISABLED**. `MIDDLEWARE_PREP_BASE=22d023a9c65b0789a0f7ee6c28548753521a9eff`,
`V3_FINAL_SHA=PENDING`. Grafana visualises; it never mutates control-plane state.

## Datasources (already provisioned, unchanged)

`codestra/provisioning/datasources/codestra.yml` provides Prometheus (`prometheus:9090`),
Loki (`loki-query:3100`), Tempo (`tempo:3200`) and the read-only Middleware Observability
datasource (`middleware-integration-api:8095`, bearer from `/run/secrets`, GET only). All are
server-proxied, immutable and TLS-verifying.

## Prepared (dark) dashboards

`scripts/generate_middleware_v3_dashboards.py` writes ten read-only dashboards to
`codestra/dashboards-prepared/middleware-v3/` - **API**, **Command Kernel**, **Workers**,
**Outbox**, **Reconciliation**, **Adapter Health**, **Dependency Health**, **Policy and
Safety Denies**, **Dead Letters**, **Incident State** (uids `mwv3-*`). No provisioning
provider and no compose file reads that folder, so Grafana never loads them until the owner
moves them under `codestra/dashboards/` after `V3_FINAL_SHA` is pinned (and raises the fixed
dashboard count in `scripts/validate_codestra_observability.py`).

Queries use only the V3 candidate metric names from Codestra-Prometheus'
`middleware-v3-metrics.v1.json` plus the metrics Middleware exposes today
(`codestra_http_*`, `codestra_auth_denials_total`, `codestra_readiness{dependency}`,
`codestra_observability_incident_events_total`), aggregate only by closed labels, read
Middleware through `GET /v1/observability/incidents?state=...`, `/platform/v1/sync/status`
and friends, and drill into Loki by structured metadata rather than labels.

## SSO audit (observed, unchanged)

`codestra/config/grafana.ini`: generic OAuth against `https://auth.codestra.co/realms/codestra`
(client `grafana-observability`, secret from `/run/secrets`), PKCE, refresh tokens, strict realm
role mapping (`observability-admin` -> GrafanaAdmin, `observability-operator` -> Editor,
`observability-viewer` -> Viewer, no role -> denied), login form, anonymous and basic auth
disabled, `rbac-policy.json` still `POLICY_PREPARED_NOT_APPLIED`. Findings recorded in the
contract: `allow_assign_grafana_admin` relies on owner control of the realm role;
`data_source_proxy_whitelist` omits `middleware-integration-api:8095` (the Infinity backend
plugin is not gated by it - confirm at activation); the Keycloak side (client and roles) was not
verified by Lane E. Nothing about production login was changed.

## Proof

`scripts/validate_middleware_v3_dashboards.py` (in `validate-codestra-observability.yml`)
regenerates the prepared dashboards and requires byte identity, checks read-only /
link-free / mutation-free panels, private datasources only, V3 metric names, allowed
aggregation labels, reviewed GET paths, darkness, and the contract's pins, datasources and
SSO observations against the live configuration.
