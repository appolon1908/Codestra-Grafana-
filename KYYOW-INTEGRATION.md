# Kyyow integration

This repository is the source authority for **grafana** in the Kyyow platform. Its Kyyow boundary is machine-readable in [kyyow-integration.v1.json](kyyow-integration.v1.json).

The component is **authenticated-ui** and its native ports are not made public by this contract. Keycloak owns identity, OpenBao owns secret delivery, and Middleware remains the sole writer to Odoo. Grafana and Superset consume read-only data paths.

This contract is source-complete but deliberately does not claim a live deployment. Production activation requires an immutable image/configuration digest, private-network verification, restore and rollback evidence, and a separately approved cutover.

## Source topology and limits

`codestra/config/grafana.ini` sets `auto_assign_org_id = 1` and maps observability roles to Viewer, Editor, or GrafanaAdmin. `codestra/provisioning/datasources/codestra.yml` configures shared backend URLs; it does not establish per-Kyyow-tenant credentials or query authorization. These are platform operator views. Dashboard variables and OAuth role mapping do not establish a tenant boundary.

All listed ports are private or loopback. This correction does not authorize runtime activation.

The provisioned Alertmanager datasource (`codestra/provisioning/datasources/codestra.yml`) is also a read dependency on `alertmanager:9093`, as declared in `codestra/runtime.v1.json`.
