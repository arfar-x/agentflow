# The knowledge catalog's MCP server.
#
#   docker build -t kb ./kb
#
# Serves two read-only tools on 8322. No published port in docker-compose.yml:
# reachability from `api` on the backend network is the access control, exactly
# as for mcp-agent-skills.
FROM python:3.12-slim

WORKDIR /opt/kb

# Dependencies first, so editing source doesn't invalidate the pip layer.
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir '.[store,mcp]'

# Migrations ship beside the code but outside the package, because psql applies
# them too (scripts/kb-init.sh). The path is explicit rather than derived: once
# the package is installed into site-packages, nothing relative to it leads back
# here.
COPY migrations/ migrations/
ENV KB_MIGRATIONS_DIR=/opt/kb/migrations

# Reachable from the backend network, not from the host -- compose publishes no
# port for this service.
ENV KB_MCP_HOST=0.0.0.0 KB_MCP_PORT=8322

USER nobody
EXPOSE 8322
CMD ["python3", "-m", "kb.adapters.inbound.mcp_server"]
