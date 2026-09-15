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
