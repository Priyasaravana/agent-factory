# syntax=docker/dockerfile:1
# The auth gateway: users, roles, sessions and API tokens, in its own container
# with its own database. The factory never sees passwords or tokens.
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/
RUN useradd --create-home --uid 10002 auth && mkdir -p /data && chown auth /data
WORKDIR /opt/auth
COPY auth/pyproject.toml ./
COPY auth/src src
RUN uv pip install --system --no-cache .
USER auth
ENV AUTH_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/auth/health')"
CMD ["factory-auth", "serve"]
