# Codestra Grafana authentication

Grafana is a human-facing observability UI, not a machine collector. Its
production and runtime-v1 configurations disable the local login form and use
Keycloak generic OAuth with PKCE. The shared Keycloak `codestra` theme supplies
the black-and-gold sign-in surface; Grafana keeps a dark default after login.

Prometheus, Alertmanager, Loki, Tempo, Telemetry, Alloy and exporters remain
private machine APIs and do not receive browser login forms.
