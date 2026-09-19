#!/usr/bin/env python3
"""Fail-closed validation of the prepared (dark) Middleware V3 dashboards and datasource contract.

Regenerates the dashboards with ``scripts/generate_middleware_v3_dashboards.py`` into a
temporary directory and requires the committed files under
``codestra/dashboards-prepared/middleware-v3`` to be byte-identical; proves every
dashboard is read-only, schema-current, link-free, mutation-free, queries only the
private datasources (Prometheus, Loki, Tempo, Middleware Observability), uses only
the V3 candidate metric names and prep-base metrics, aggregates only by allowed
low-cardinality labels, reads the Middleware API with GET on reviewed paths, and
that nothing provisions or mounts the prepared folder. Also validates
``codestra/contracts/middleware-v3-dashboards.v1.json`` (pins, datasource contract,
SSO audit) against ``codestra/provisioning/datasources/codestra.yml`` and
``codestra/config/grafana.ini``. PyYAML is the only dependency.
"""

from __future__ import annotations

import configparser
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CODESTRA = ROOT / "codestra"
PREPARED = CODESTRA / "dashboards-prepared" / "middleware-v3"
GENERATOR = ROOT / "scripts" / "generate_middleware_v3_dashboards.py"
CONTRACT = CODESTRA / "contracts" / "middleware-v3-dashboards.v1.json"
DATASOURCES = CODESTRA / "provisioning" / "datasources" / "codestra.yml"
DASHBOARD_PROVISIONING = CODESTRA / "provisioning" / "dashboards" / "codestra.yml"
COMPOSE = CODESTRA / "deploy" / "compose.candidate.yaml"
RUNTIME_COMPOSE = CODESTRA / "runtime-v1" / "compose.yaml"
INI = CODESTRA / "config" / "grafana.ini"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
METRIC_TOKEN = re.compile(r"\b((?:middleware|codestra)_[a-z0-9_]+|alertmanager_[a-z0-9_]+|up)\b")
BY_CLAUSE = re.compile(r"\bby\s*\(([^)]*)\)")
LABEL_MATCHER = re.compile(r"\{([^}]*)\}")
ALLOWED_DATASOURCES = {"codestra-prometheus", "codestra-loki", "codestra-tempo", "codestra-middleware-observability"}
ALLOWED_READ_PATHS = ("/v1/observability/incidents", "/platform/v1/sync/status", "/platform/v1/services", "/v1/observability/secrets/health")
FORBIDDEN_TOKENS = {"tenant_id=", "customer_id=", "account_id=", "user_id=", "command_id=", "operation_id=", "correlation_id=", "email=", "phone="}
REQUIRED_TITLES = {
    "Middleware V3 — API", "Middleware V3 — Command Kernel", "Middleware V3 — Workers", "Middleware V3 — Outbox",
    "Middleware V3 — Reconciliation", "Middleware V3 — Adapter Health", "Middleware V3 — Dependency Health",
    "Middleware V3 — Policy and Safety Denies", "Middleware V3 — Dead Letters", "Middleware V3 — Incident State",
}
MUTATION_PANELS = {"button", "actions", "form-panel", "volkovlabs-form-panel", "canvas"}


def fail(message: str) -> None:
    print(f"MIDDLEWARE_V3_DASHBOARDS_ERROR={message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid JSON {path.relative_to(ROOT)}: {exc}")


def load_yaml(path: Path) -> Any:
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        fail(f"invalid YAML {path.relative_to(ROOT)}: {exc}")


def generator_module():
    spec = importlib.util.spec_from_file_location("generate_middleware_v3_dashboards", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_no_drift() -> list[Path]:
    committed = sorted(PREPARED.glob("*.json"))
    if len(committed) != 10:
        fail(f"expected exactly 10 prepared dashboards, found {len(committed)}")
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run([sys.executable, str(GENERATOR), folder], check=True, capture_output=True)
        generated = sorted(Path(folder).glob("*.json"))
        if [p.name for p in generated] != [p.name for p in committed]:
            fail("prepared dashboard set differs from the generator output")
        for left, right in zip(committed, generated):
            if left.read_bytes().replace(b"\r\n", b"\n") != right.read_bytes().replace(b"\r\n", b"\n"):
                fail(f"prepared dashboard drifted from its generator: {left.name}")
    return committed


def targets(panel: dict[str, Any]):
    for target in panel.get("targets", []):
        yield target


def validate_dashboards(paths: list[Path], allowed_metrics: set[str], allowed_labels: set[str]) -> None:
    uids: set[str] = set()
    titles: set[str] = set()
    for path in paths:
        dashboard = load_json(path)
        uid, title = dashboard.get("uid"), dashboard.get("title")
        if not str(uid).startswith("mwv3-") or uid in uids:
            fail(f"{path.name}: uid must be unique and start with mwv3-")
        if not title or title in titles:
            fail(f"{path.name}: missing or duplicate title")
        uids.add(uid)
        titles.add(title)
        if dashboard.get("editable") is not False:
            fail(f"{path.name}: prepared dashboards must be read-only")
        if dashboard.get("schemaVersion", 0) < 39:
            fail(f"{path.name}: schema outdated")
        if dashboard.get("links") not in ([], None):
            fail(f"{path.name}: no external or action links")
        if "prepared" not in dashboard.get("tags", []) or "middleware-v3" not in dashboard.get("tags", []):
            fail(f"{path.name}: must be tagged prepared + middleware-v3")
        if "PREPARED / DARK" not in dashboard.get("description", ""):
            fail(f"{path.name}: description must state PREPARED / DARK")
        panels = dashboard.get("panels") or []
        if not panels:
            fail(f"{path.name}: no panels")
        serialized = json.dumps(dashboard, sort_keys=True).lower()
        for token in FORBIDDEN_TOKENS:
            if token in serialized:
                fail(f"{path.name}: forbidden query token {token}")
        for panel in panels:
            if panel.get("type") in MUTATION_PANELS:
                fail(f"{path.name}: mutation-capable panel {panel['type']}")
            ds = panel.get("datasource", {}).get("uid")
            if ds not in ALLOWED_DATASOURCES:
                fail(f"{path.name}: panel {panel.get('title')} uses datasource {ds}")
            for target in targets(panel):
                tds = target.get("datasource", {}).get("uid")
                if tds not in ALLOWED_DATASOURCES:
                    fail(f"{path.name}: target datasource {tds}")
                if tds == "codestra-middleware-observability":
                    options = target.get("url_options", {})
                    if options.get("method") != "GET" or options.get("data"):
                        fail(f"{path.name}: Middleware Observability targets must be GET without a body")
                    if not str(target.get("url", "")).startswith(ALLOWED_READ_PATHS):
                        fail(f"{path.name}: Middleware read path {target.get('url')} is not reviewed")
                expr = str(target.get("expr", ""))
                if tds == "codestra-prometheus" and expr:
                    unknown = sorted(m for m in set(METRIC_TOKEN.findall(expr)) if m not in allowed_metrics and not any(m == f"{c}_{s}" for c in allowed_metrics for s in ("bucket", "sum", "count")))
                    if unknown:
                        fail(f"{path.name}: unexpected metrics {unknown}")
                    for clause in BY_CLAUSE.findall(expr):
                        labels = {item.strip() for item in clause.split(",") if item.strip()}
                        if labels - allowed_labels:
                            fail(f"{path.name}: aggregates by disallowed labels {sorted(labels - allowed_labels)}")
                if tds == "codestra-loki" and expr:
                    for selector in LABEL_MATCHER.findall(expr.split("|")[0]):
                        for item in selector.split(","):
                            key = item.split("=")[0].strip().rstrip("!~")
                            if key not in {"service", "environment", "application", "codestra_business", "log_source", "level"}:
                                fail(f"{path.name}: Loki stream selector on non-label {key}")
    if titles != REQUIRED_TITLES:
        fail(f"prepared dashboard titles drift: {sorted(titles ^ REQUIRED_TITLES)}")


def validate_dark() -> None:
    for path in (DASHBOARD_PROVISIONING, COMPOSE, RUNTIME_COMPOSE):
        if path.is_file() and "dashboards-prepared" in path.read_text(encoding="utf-8"):
            fail(f"{path.relative_to(ROOT)} must not provision or mount the prepared folder")
    for provider in load_yaml(DASHBOARD_PROVISIONING).get("providers", []):
        options = provider.get("options", {})
        if options.get("foldersFromFilesStructure") is not False or "prepared" in str(options.get("path", "")):
            fail("dashboard providers must stay explicit and never read the prepared folder")


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("contract_id") != "middleware-v3-dashboards" or contract.get("status") != "PREPARED_DISABLED":
        fail("contract identity or status drift")
    if contract.get("activation_enabled") is not False:
        fail("activation_enabled must be false")
    middleware = contract.get("middleware", {})
    if not SHA40.fullmatch(str(middleware.get("prep_base_sha", ""))):
        fail("middleware.prep_base_sha must be a 40-hex commit")
    if middleware.get("v3_final_sha") != "PENDING" and not SHA40.fullmatch(str(middleware.get("v3_final_sha"))):
        fail("middleware.v3_final_sha must be PENDING or a 40-hex commit")

    sources = {s["uid"]: s for s in load_yaml(DATASOURCES).get("datasources", [])}
    declared = contract.get("datasources", {})
    for uid in ("codestra-prometheus", "codestra-loki", "codestra-tempo"):
        if uid not in declared or uid not in sources:
            fail(f"datasource {uid} must be declared and provisioned")
        if declared[uid].get("url") != sources[uid].get("url") or declared[uid].get("type") != sources[uid].get("type"):
            fail(f"datasource {uid} drift between contract and provisioning")
        if sources[uid].get("access") != "proxy" or sources[uid].get("editable") is not False:
            fail(f"datasource {uid} must be server-proxied and immutable")
        if sources[uid].get("jsonData", {}).get("tlsSkipVerify") is not False:
            fail(f"datasource {uid} may not skip TLS verification")
    middleware_ds = sources.get("codestra-middleware-observability", {})
    if not str(middleware_ds.get("secureJsonData", {}).get("bearerToken", "")).startswith("$__file{/run/secrets/"):
        fail("the Middleware Observability bearer must be a $__file secret expansion")
    if middleware_ds.get("jsonData", {}).get("oauthPassThru") is not False:
        fail("the Middleware Observability datasource must not pass user OAuth tokens through")

    dashboards = contract.get("dashboards", [])
    if {d.get("title") for d in dashboards} != REQUIRED_TITLES or any(d.get("status") != "PREPARED_DISABLED" for d in dashboards):
        fail("contract dashboard inventory drift")

    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read_string("[default]\n" + INI.read_text(encoding="utf-8"))  # grafana.ini opens with section-less keys
    audit = contract.get("sso_audit", {})
    checks = {
        "generic_oauth_enabled": ini.get("auth.generic_oauth", "enabled", fallback="") == "true",
        "client_id": ini.get("auth.generic_oauth", "client_id", fallback=""),
        "client_secret_from_file": ini.get("auth.generic_oauth", "client_secret", fallback="").startswith("$__file{/run/secrets/"),
        "pkce": ini.get("auth.generic_oauth", "use_pkce", fallback="") == "true",
        "role_attribute_strict": ini.get("auth.generic_oauth", "role_attribute_strict", fallback="") == "true",
        "login_form_disabled": ini.get("auth", "disable_login_form", fallback="") == "true",
        "anonymous_disabled": ini.get("auth.anonymous", "enabled", fallback="") == "false",
        "basic_auth_disabled": ini.get("auth.basic", "enabled", fallback="") == "false",
        "issuer_realm": "https://auth.codestra.co/realms/codestra" if ini.get("auth.generic_oauth", "auth_url", fallback="").startswith("https://auth.codestra.co/realms/codestra/") else "",
    }
    for key, value in checks.items():
        if audit.get("observed", {}).get(key) != value:
            fail(f"sso_audit.observed.{key} does not match grafana.ini ({value!r})")
    if audit.get("changed_by_lane_e") is not False or audit.get("production_login_changed") is not False:
        fail("Lane E must not change the production login")


def main() -> None:
    module = generator_module()
    paths = validate_no_drift()
    validate_dashboards(paths, module.V3_METRICS | module.PREP_BASE_METRICS | {"alertmanager_notifications_failed_total"}, module.ALLOWED_BY_LABELS)
    validate_dark()
    contract = load_json(CONTRACT)
    validate_contract(contract)
    print(
        "MIDDLEWARE_V3_DASHBOARDS=PASS dashboards=10 status=PREPARED_DISABLED "
        f"prep_base={contract['middleware']['prep_base_sha'][:12]} v3_final={contract['middleware']['v3_final_sha']}"
    )


if __name__ == "__main__":
    main()
