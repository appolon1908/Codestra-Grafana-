# syntax=docker/dockerfile:1.7

ARG PYTHON_BUILDER_IMAGE
ARG GRAFANA_IMAGE

FROM ${PYTHON_BUILDER_IMAGE} AS dashboard-builder
WORKDIR /workspace
COPY codestra/business-registry.json codestra/business-registry.json
COPY scripts/generate_codestra_dashboards.py scripts/generate_codestra_dashboards.py
RUN python3 -m py_compile scripts/generate_codestra_dashboards.py \
    && python3 scripts/generate_codestra_dashboards.py \
    && test "$(find codestra/dashboards -type f -name '*.json' | wc -l)" -ge 40

FROM ${GRAFANA_IMAGE}
LABEL org.opencontainers.image.title="Codestra Corporate Grafana"
LABEL org.opencontainers.image.description="Source-controlled Codestra dashboards, datasources, SSO, and corporate operating boundaries"
LABEL org.opencontainers.image.vendor="Codestra"

# Signed Infinity datasource plugin, pinned by version and package digest
# (codestra/release/plugin-lock.json). It is the only plugin in the image and
# serves solely the read-only Middleware Observability datasource.
USER root
RUN set -eu \
    && mkdir -p /var/lib/grafana/plugins /tmp/plugin \
    && wget -q -O /tmp/plugin/infinity.zip "https://grafana.com/api/plugins/yesoreyeram-infinity-datasource/versions/4.0.0/download?os=linux&arch=amd64" \
    && echo "2ebce2b2937983651d64991e833e48294b8002581a105741ae3df967e329905d  /tmp/plugin/infinity.zip" | sha256sum -c - \
    && unzip -q /tmp/plugin/infinity.zip -d /var/lib/grafana/plugins \
    && rm -rf /tmp/plugin \
    && chown -R 472:0 /var/lib/grafana/plugins

COPY --chown=472:0 --chmod=0444 codestra/config/grafana.ini /etc/grafana/grafana.ini
COPY --chown=472:0 codestra/provisioning/ /etc/grafana/provisioning/
COPY --from=dashboard-builder --chown=472:0 codestra/dashboards/ /etc/grafana/codestra-dashboards/

USER 472:0
