FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.28
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN uv sync --locked --no-dev
RUN chmod +x /app/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/api/health/ready', timeout=3)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
