FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_BRIDGE_STATE_DIR=/var/lib/agent-bridge

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
# The private hub exposes coordinator intake as well as Manual Bridge. The
# coordinator extra supplies the stable Python Codex SDK and its pinned runtime;
# Manual Bridge remains available if coordinator activation is disabled/fails.
RUN python -m pip install --no-cache-dir '.[codex]'

RUN addgroup --system bridge && adduser --system --ingroup bridge bridge \
    && mkdir -p /var/lib/agent-bridge && chown -R bridge:bridge /var/lib/agent-bridge
USER bridge

EXPOSE 58081
CMD ["agent-bridge-catalog", "--bind", "0.0.0.0", "--port", "58081"]
