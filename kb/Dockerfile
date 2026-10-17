# The knowledge catalog: its MCP server, scheduler, webhook receiver, CLI and
# the admin console, all one image started with different commands.
#
#   docker build -t kb ./kb
#
# The MCP server serves two read-only tools on 8322. No published port in
# docker-compose.yml: reachability from `api` on the backend network is the
# access control, exactly as for mcp-agent-skills.

# The admin console's web app is built here and only the result is kept, so no
# Node toolchain ships in the runtime image. `npm ci` installs exactly what
# package-lock.json says; a build that cannot reproduce it fails instead of
# quietly using something else.
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /opt/kb

# Dependencies first, so editing source doesn't invalidate the pip layer.
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir '.[store,mcp,sources]'

# Migrations ship beside the code but outside the package, because psql applies
# them too (scripts/kb-init.sh). The path is explicit rather than derived: once
# the package is installed into site-packages, nothing relative to it leads back
# here.
COPY migrations/ migrations/
ENV KB_MIGRATIONS_DIR=/opt/kb/migrations

# The built admin console, served by kb.adapters.inbound.admin.
COPY --from=web /web/dist /opt/kb/web
ENV KB_ADMIN_STATIC_DIR=/opt/kb/web

# Reachable from the backend network, not from the host -- compose publishes no
# port for this service.
ENV KB_MCP_HOST=0.0.0.0 KB_MCP_PORT=8322

USER nobody
EXPOSE 8322
CMD ["python3", "-m", "kb.adapters.inbound.mcp_server"]
