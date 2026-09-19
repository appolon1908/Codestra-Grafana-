#!/usr/bin/env python3
"""Generate the prepared (dark) Middleware V3 dashboards.

The dashboards are written to ``codestra/dashboards-prepared/middleware-v3`` which no
provisioning provider and no compose mount reads, so Grafana never loads them until
``V3_FINAL_SHA`` is pinned and the owner moves the folder under
``codestra/dashboards/platform`` (bumping the generated-dashboard count in
``scripts/validate_codestra_observability.py``). They reuse the reviewed panel
helpers of ``scripts/generate_codestra_dashboards.py``, query only the private
datasources (Prometheus, Loki, Tempo, Middleware Observability), use the V3
candidate metric names fixed in Codestra-Prometheus
``codestra/contracts/middleware-v3-metrics.v1.json`` and aggregate only by
low-cardinality labels. Every dashboard is read-only; nothing mutates control-plane
state.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "codestra" / "dashboards-prepared" / "middleware-v3"
SPEC = importlib.util.spec_from_file_location("codestra_dashboards", ROOT / "scripts" / "generate_codestra_dashboards.py")
assert SPEC is not None and SPEC.loader is not None
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)

JOB = 'job="codestra-middleware-metrics"'
LOKI_MW = '{service="middleware", environment="production"}'
TEMPO_UID = "codestra-tempo"
MIDDLEWARE_PREP_BASE = "22d023a9c65b0789a0f7ee6c28548753521a9eff"
TAGS = ["middleware-v3", "prepared", "middleware", "corporate", "Codestra"]

# V3 candidate metric names (Codestra-Prometheus middleware-v3-metrics.v1.json) plus the
# metrics already exposed at the prep base that the API and dependency views rely on.
V3_METRICS = {
    "middleware_commands_received_total", "middleware_commands_completed_total", "middleware_commands_failed_total",
    "middleware_commands_reconciliation_required_total", "middleware_command_duration_seconds",
    "middleware_idempotency_duplicates_total", "middleware_policy_denials_total", "middleware_safety_denials_total",
    "middleware_outbox_backlog", "middleware_outbox_oldest_seconds", "middleware_active_leases",
    "middleware_lease_expirations_total", "middleware_adapter_requests_total", "middleware_adapter_failures_total",
    "middleware_adapter_latency_seconds", "middleware_dead_letters", "middleware_reconciliation_backlog",
    "middleware_dependency_up",
}
PREP_BASE_METRICS = {
    "codestra_http_requests_total", "codestra_http_request_duration_seconds", "codestra_http_active_requests",
    "codestra_auth_denials_total", "codestra_readiness", "codestra_observability_incident_events_total", "up",
}
ALLOWED_BY_LABELS = {"service", "environment", "adapter", "command_family", "state", "result", "stage", "reason", "mode", "operation", "dependency", "le", "status", "method", "severity", "alertname"}


def tempo_search_panel(panel_id: int, title: str, query: str, x: int, y: int, *, width: int = 24, height: int = 8) -> dict[str, Any]:
    """Read-only TraceQL search over the private Tempo datasource."""
    return {
        "id": panel_id,
        "title": title,
        "type": "table",
        "datasource": G.datasource(TEMPO_UID, "tempo"),
        "targets": [{"refId": "A", "datasource": G.datasource(TEMPO_UID, "tempo"), "queryType": "traceql", "query": query, "limit": 20, "tableType": "traces"}],
        "gridPos": {"h": height, "w": width, "x": x, "y": y},
        "options": {"showHeader": True},
        "fieldConfig": {"defaults": {}, "overrides": []},
    }


def dashboard(uid: str, title: str, description: str, panels: list[dict[str, Any]], *, extra_tags: list[str] | None = None) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "tags": TAGS + (extra_tags or []),
        "timezone": "utc",
        "schemaVersion": 39,
        "version": 1,
        "editable": False,
        "graphTooltip": 1,
        "time": {"from": "now-6h", "to": "now"},
        "refresh": "1m",
        "links": [],
        "templating": {"list": [G.constant_variable("codestra_business", "Business", "platform"), G.constant_variable("environment", "Environment", "production")]},
        "panels": panels,
        "description": f"{description} PREPARED / DARK for Middleware V3 (prep base {MIDDLEWARE_PREP_BASE[:12]}, V3_FINAL_SHA pending); read-only.",
    }


def ratio(numerator: str, denominator: str, by: str) -> str:
    return f"sum by ({by}) (rate({numerator}{{{JOB}}}[5m])) / clamp_min(sum by ({by}) (rate({denominator}{{{JOB}}}[5m])), 0.001)"


def api() -> dict[str, Any]:
    return dashboard("mwv3-middleware-api", "Middleware V3 — API", "Ingress health of the Middleware API: request rate, error ratio, latency, auth denials and the monitoring-readonly scrape.", [
        G.stat_panel(1, "Requests / s", f'sum(rate(codestra_http_requests_total{{{JOB}}}[5m]))', 0, 0, unit="reqps"),
        G.stat_panel(2, "5xx ratio", f'sum(rate(codestra_http_requests_total{{{JOB},status=~"5.."}}[5m])) / clamp_min(sum(rate(codestra_http_requests_total{{{JOB}}}[5m])), 0.001)', 6, 0, unit="percentunit", thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 0.02}]),
        G.stat_panel(3, "p95 latency", f'histogram_quantile(0.95, sum by (le) (rate(codestra_http_request_duration_seconds_bucket{{{JOB}}}[5m])))', 12, 0, unit="s"),
        G.stat_panel(4, "Scrape up (monitoring-readonly)", f'up{{{JOB}}}', 18, 0, thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}]),
        G.time_series_panel(5, "Requests by status class", f'sum by (status) (rate(codestra_http_requests_total{{{JOB}}}[5m]))', 0, 6, unit="reqps", legend="{{status}}"),
        G.time_series_panel(6, "Auth denials by result", f'sum by (result) (rate(codestra_auth_denials_total{{{JOB}}}[5m]))', 12, 6, unit="short", legend="{{result}}"),
        G.time_series_panel(7, "Active requests", f'sum(codestra_http_active_requests{{{JOB}}})', 0, 14, unit="short", legend="active"),
        G.logs_panel(8, "API errors (structured; correlation_id drilldown)", f'{LOKI_MW} | json | level=~"error|critical"', 12, 14),
    ], extra_tags=["api"])


def command_kernel() -> dict[str, Any]:
    return dashboard("mwv3-command-kernel", "Middleware V3 — Command Kernel", "Command intake, completion, failure and reconciliation by command family, idempotent replays and kernel stage latency.", [
        G.stat_panel(1, "Commands received / s", f'sum(rate(middleware_commands_received_total{{{JOB}}}[5m]))', 0, 0, unit="ops"),
        G.stat_panel(2, "Completion ratio", ratio("middleware_commands_completed_total", "middleware_commands_received_total", "service"), 6, 0, unit="percentunit"),
        G.stat_panel(3, "Failure ratio", ratio("middleware_commands_failed_total", "middleware_commands_received_total", "service"), 12, 0, unit="percentunit", thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 0.05}]),
        G.stat_panel(4, "Idempotent replays (5m)", f'sum(increase(middleware_idempotency_duplicates_total{{{JOB}}}[5m]))', 18, 0),
        G.time_series_panel(5, "Received by command family", f'sum by (command_family) (rate(middleware_commands_received_total{{{JOB}}}[5m]))', 0, 6, unit="ops", legend="{{command_family}}"),
        G.time_series_panel(6, "Failed by family and result", f'sum by (command_family, result) (rate(middleware_commands_failed_total{{{JOB}}}[5m]))', 12, 6, unit="ops", legend="{{command_family}} / {{result}}"),
        G.time_series_panel(7, "Reconciliation required by family", f'sum by (command_family) (rate(middleware_commands_reconciliation_required_total{{{JOB}}}[5m]))', 0, 14, unit="ops", legend="{{command_family}}"),
        G.time_series_panel(8, "Kernel stage p95", f'histogram_quantile(0.95, sum by (le, stage) (rate(middleware_command_duration_seconds_bucket{{{JOB}}}[5m])))', 12, 14, unit="s", legend="{{stage}}"),
        tempo_search_panel(9, "Recent kernel traces (TraceQL, correlation.id drilldown)", '{ resource.service.name = "middleware" && name =~ "middleware\\\\.(policy|safety|outbox|adapter)\\\\..*" }', 0, 22),
    ], extra_tags=["kernel"])


def workers() -> dict[str, Any]:
    return dashboard("mwv3-workers", "Middleware V3 — Workers", "Worker lease health: active leases, expirations and worker log errors.", [
        G.stat_panel(1, "Active leases", f'sum(middleware_active_leases{{{JOB}}})', 0, 0),
        G.stat_panel(2, "Lease expirations (15m)", f'sum(increase(middleware_lease_expirations_total{{{JOB}}}[15m]))', 6, 0, thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 5}, {"color": "red", "value": 20}]),
        G.stat_panel(3, "Worker scrape up", f'min(up{{{JOB}}})', 12, 0, thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}]),
        G.time_series_panel(4, "Active leases", f'sum by (service) (middleware_active_leases{{{JOB}}})', 0, 6, unit="short"),
        G.time_series_panel(5, "Lease expirations / min", f'sum by (service) (increase(middleware_lease_expirations_total{{{JOB}}}[1m]))', 12, 6, unit="short"),
        G.logs_panel(6, "Worker lease events", f'{LOKI_MW} | json | lease_outcome != ""', 0, 14),
    ], extra_tags=["workers"])


def outbox() -> dict[str, Any]:
    return dashboard("mwv3-outbox", "Middleware V3 — Outbox", "Outbox backlog and age; rows awaiting dispatch are the leading indicator of a stalled effect path.", [
        G.stat_panel(1, "Backlog rows", f'sum(middleware_outbox_backlog{{{JOB}}})', 0, 0, thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 500}, {"color": "red", "value": 1000}]),
        G.stat_panel(2, "Oldest pending", f'max(middleware_outbox_oldest_seconds{{{JOB}}})', 6, 0, unit="s", thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 300}, {"color": "red", "value": 600}]),
        G.stat_panel(3, "Active leases", f'sum(middleware_active_leases{{{JOB}}})', 12, 0),
        G.stat_panel(4, "Dead letters", f'sum(middleware_dead_letters{{{JOB}}})', 18, 0, thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]),
        G.time_series_panel(5, "Backlog", f'sum by (service) (middleware_outbox_backlog{{{JOB}}})', 0, 6, unit="short"),
        G.time_series_panel(6, "Oldest pending row age", f'max by (service) (middleware_outbox_oldest_seconds{{{JOB}}})', 12, 6, unit="s"),
    ], extra_tags=["outbox"])


def reconciliation() -> dict[str, Any]:
    return dashboard("mwv3-reconciliation", "Middleware V3 — Reconciliation", "Operations awaiting reconciliation and the reconciler's decisions per adapter; desired vs observed state read from the Middleware control plane.", [
        G.stat_panel(1, "Reconciliation backlog", f'sum(middleware_reconciliation_backlog{{{JOB}}})', 0, 0, thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 20}, {"color": "red", "value": 100}]),
        G.stat_panel(2, "Parked for reconciliation (1h)", f'sum(increase(middleware_commands_reconciliation_required_total{{{JOB}}}[1h]))', 6, 0),
        G.time_series_panel(3, "Backlog", f'sum by (service) (middleware_reconciliation_backlog{{{JOB}}})', 0, 6, unit="short"),
        G.time_series_panel(4, "Parked by adapter", f'sum by (adapter) (rate(middleware_commands_reconciliation_required_total{{{JOB}}}[5m]))', 12, 6, unit="ops", legend="{{adapter}}"),
        G.infinity_table(5, "Component reconciliation (desired vs observed)", "/platform/v1/sync/status?environment=production", 0, 14, root_selector="data"),
    ], extra_tags=["reconciliation"])


def adapter_health() -> dict[str, Any]:
    return dashboard("mwv3-adapter-health", "Middleware V3 — Adapter Health", "Provider adapters: request rate, failure ratio and latency per adapter; readback outcomes.", [
        G.stat_panel(1, "Adapter requests / s", f'sum(rate(middleware_adapter_requests_total{{{JOB}}}[5m]))', 0, 0, unit="ops"),
        G.stat_panel(2, "Failure ratio", ratio("middleware_adapter_failures_total", "middleware_adapter_requests_total", "service"), 6, 0, unit="percentunit", thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 0.2}]),
        G.stat_panel(3, "p95 adapter latency", f'histogram_quantile(0.95, sum by (le) (rate(middleware_adapter_latency_seconds_bucket{{{JOB}}}[5m])))', 12, 0, unit="s"),
        G.time_series_panel(4, "Requests by adapter", f'sum by (adapter) (rate(middleware_adapter_requests_total{{{JOB}}}[5m]))', 0, 6, unit="ops", legend="{{adapter}}"),
        G.time_series_panel(5, "Failures by adapter and result", f'sum by (adapter, result) (rate(middleware_adapter_failures_total{{{JOB}}}[5m]))', 12, 6, unit="ops", legend="{{adapter}} / {{result}}"),
        G.time_series_panel(6, "p95 latency by adapter", f'histogram_quantile(0.95, sum by (le, adapter) (rate(middleware_adapter_latency_seconds_bucket{{{JOB}}}[5m])))', 0, 14, unit="s", legend="{{adapter}}"),
        G.table_panel(7, "Requests by adapter and operation (1h)", f'sum by (adapter, operation) (increase(middleware_adapter_requests_total{{{JOB}}}[1h]))', 12, 14, width=12, legend="{{adapter}} / {{operation}}"),
    ], extra_tags=["adapters"])


def dependency_health() -> dict[str, Any]:
    return dashboard("mwv3-dependency-health", "Middleware V3 — Dependency Health", "Readiness of every required dependency (PostgreSQL, Redis, NATS, OpenBao, Keycloak, adapters) as reported by Middleware.", [
        G.stat_panel(1, "Dependencies not ready", f'count(codestra_readiness{{{JOB}}} == 0) or vector(0)', 0, 0, thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]),
        G.stat_panel(2, "Middleware scrape up", f'up{{{JOB}}}', 6, 0, thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}]),
        G.table_panel(3, "Readiness by dependency (prep base: codestra_readiness)", f'min by (dependency) (codestra_readiness{{{JOB}}})', 0, 6, legend="{{dependency}}"),
        G.time_series_panel(4, "Dependency up (V3 candidate: middleware_dependency_up)", f'min by (dependency) (middleware_dependency_up{{{JOB}}})', 0, 14, unit="short", legend="{{dependency}}"),
        G.time_series_panel(5, "Readiness over time", f'min by (dependency) (codestra_readiness{{{JOB}}})', 12, 14, unit="short", legend="{{dependency}}"),
    ], extra_tags=["dependencies"])


def policy_safety() -> dict[str, Any]:
    return dashboard("mwv3-policy-safety-denies", "Middleware V3 — Policy and Safety Denies", "Policy Engine and Safety Gate denials by closed reason code; a surge means a caller keeps attempting a blocked effect.", [
        G.stat_panel(1, "Policy denials (15m)", f'sum(increase(middleware_policy_denials_total{{{JOB}}}[15m]))', 0, 0, thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 50}]),
        G.stat_panel(2, "Safety denials (15m)", f'sum(increase(middleware_safety_denials_total{{{JOB}}}[15m]))', 6, 0, thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 10}]),
        G.time_series_panel(3, "Policy denials by reason", f'sum by (reason) (rate(middleware_policy_denials_total{{{JOB}}}[5m]))', 0, 6, unit="ops", legend="{{reason}}"),
        G.time_series_panel(4, "Safety denials by reason", f'sum by (reason) (rate(middleware_safety_denials_total{{{JOB}}}[5m]))', 12, 6, unit="ops", legend="{{reason}}"),
        G.logs_panel(5, "Denial events (structured)", f'{LOKI_MW} | json | policy_result="deny" or safety_result="deny"', 0, 14),
    ], extra_tags=["policy", "safety"])


def dead_letters() -> dict[str, Any]:
    return dashboard("mwv3-dead-letters", "Middleware V3 — Dead Letters", "Dead-lettered outbox rows awaiting an operator decision (replay or resolve); never an automatic provider effect.", [
        G.stat_panel(1, "Dead letters", f'sum(middleware_dead_letters{{{JOB}}})', 0, 0, thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]),
        G.stat_panel(2, "Reconciliation backlog", f'sum(middleware_reconciliation_backlog{{{JOB}}})', 6, 0),
        G.time_series_panel(3, "Dead letters over time", f'sum by (service) (middleware_dead_letters{{{JOB}}})', 0, 6, unit="short"),
        G.logs_panel(4, "Dead-letter events (operation_id drilldown)", f'{LOKI_MW} | json | outbox_result="dead_letter"', 0, 14),
    ], extra_tags=["dead-letters"])


def incident_state() -> dict[str, Any]:
    return dashboard("mwv3-incident-state", "Middleware V3 — Incident State", "Durable incidents (OPEN/ACK/RESOLVED/SUPPRESSED) read from the Middleware incident authority; Alertmanager deliveries deduplicated per fingerprint.", [
        G.infinity_table(1, "Firing incidents (OPEN)", "/v1/observability/incidents?state=firing&limit=100", 0, 0, root_selector="items"),
        G.infinity_table(2, "Acknowledged incidents (ACK)", "/v1/observability/incidents?state=acknowledged&limit=100", 0, 8, root_selector="items"),
        G.infinity_table(3, "Suppressed incidents (inhibited / silenced)", "/v1/observability/incidents?state=inhibited&limit=100", 0, 16, root_selector="items", height=6),
        G.stat_panel(4, "Incident events accepted (5m)", f'sum(increase(codestra_observability_incident_events_total{{result="accepted"}}[5m]))', 0, 22),
        G.stat_panel(5, "Duplicate deliveries (5m)", f'sum(increase(codestra_observability_incident_events_total{{result="duplicate"}}[5m]))', 6, 22, thresholds=[{"color": "green", "value": None}]),
        G.stat_panel(6, "Alertmanager -> Middleware failures (10m)", "sum(increase(alertmanager_notifications_failed_total[10m]))", 12, 22, thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]),
    ], extra_tags=["incident"])


DASHBOARDS = [api, command_kernel, workers, outbox, reconciliation, adapter_health, dependency_health, policy_safety, dead_letters, incident_state]


def render() -> dict[str, dict[str, Any]]:
    return {f"{build.__name__.replace('_', '-')}.json": build() for build in DASHBOARDS}


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    if target.exists():
        for path in target.glob("*.json"):
            path.unlink()
    target.mkdir(parents=True, exist_ok=True)
    for name, payload in render().items():
        (target / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"generated {len(DASHBOARDS)} prepared Middleware V3 dashboards under {target}")


if __name__ == "__main__":
    main()
