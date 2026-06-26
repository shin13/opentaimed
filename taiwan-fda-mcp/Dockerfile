# path: Dockerfile
# brief: Non-root container for the shared HTTP MCP service (ADR-0010 Model B).

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Non-root runtime user (security invariant #3).
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

# Install deps first (cached layer), then the project.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev \
    && chown -R appuser:appuser /app

# Shared-service defaults; 0.0.0.0 binds all interfaces INSIDE the container
# (the port is never published to the host — see docker-compose.yml).
ENV MCP_TRANSPORT=http \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8765 \
    MCP_HTTP_PATH=/mcp/ \
    UV_NO_SYNC=1

USER appuser
EXPOSE 8765
CMD ["uv", "run", "taiwan-fda-mcp-server"]
