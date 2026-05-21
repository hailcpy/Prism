FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /uvx /bin/

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY . .
RUN uv sync --locked --no-dev --package chatbot-api

CMD ["uvicorn", "chatbot_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
