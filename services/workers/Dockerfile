FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
# No external deps in Phase 0 — add redis-py, psycopg etc. in Phase 2.
RUN pip install --no-cache-dir hatchling

COPY log_writer/     log_writer/
COPY metrics_roller/ metrics_roller/
COPY metrics_reconciler/ metrics_reconciler/
COPY partition_cron/ partition_cron/

# Default CMD is overridden per service in docker-compose.yml.
CMD ["python", "-m", "log_writer.main"]
