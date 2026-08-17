FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_BRIDGE_STATE_DIR=/var/lib/agent-bridge

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN python -m pip install --no-cache-dir '.[codex]'

RUN addgroup --system bridge && adduser --system --ingroup bridge bridge \
    && mkdir -p /var/lib/agent-bridge && chown -R bridge:bridge /var/lib/agent-bridge
USER bridge

EXPOSE 58081
CMD ["sh", "-c", "alembic upgrade head && exec agent-bridge serve --bind 0.0.0.0 --port 58081"]
