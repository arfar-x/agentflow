# mcp-figma: Framelink's `figma-developer-mcp`
# (github.com/GLips/Figma-Context-MCP), a community MCP server over Figma's
# public REST API, served to LibreChat over streamable HTTP. Why this and not
# Figma's own hosted server (mcp.figma.com): see docs/FIGMA.md.
#
# This image only installs the pinned package. Every runtime setting (host,
# port, telemetry off, no image downloads) is set in docker-compose.yml, same
# as every other service in this stack.
FROM node:24.21.0-alpine3.24

# Bump this deliberately, the same way the agent-skills submodule pin is
# bumped: a reviewable one-line change, then `make build SERVICE=mcp-figma`.
# See docs/FIGMA.md "Upgrading".
ARG FIGMA_DEVELOPER_MCP_VERSION=0.13.2

# --ignore-scripts: neither the package nor its runtime dependencies need an
# install script, so none gets to run at build time.
RUN npm install --global --omit=dev --ignore-scripts \
      "figma-developer-mcp@${FIGMA_DEVELOPER_MCP_VERSION}" \
 && npm cache clean --force

USER node
WORKDIR /home/node
EXPOSE 3333
CMD ["figma-developer-mcp"]
