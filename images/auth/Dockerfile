# syntax=docker/dockerfile:1
# The auth gateway: users, roles, sessions and API tokens, in its own container
# with its own database. The factory never sees passwords or tokens.
FROM python:3.12-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64
COPY --from=ghcr.io/astral-sh/uv:0.8@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6 /uv /usr/local/bin/
RUN useradd --create-home --uid 10002 auth && mkdir -p /data && chown auth /data
WORKDIR /opt/auth
COPY auth/pyproject.toml auth/uv.lock ./
COPY auth/src src
# Exactly the lockfile's versions, each checked against its hash (S-07); the project last, without deps.
RUN uv export --frozen --no-dev --no-emit-project -q -o /tmp/requirements.txt \
 && uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt \
 && uv pip install --system --no-cache --no-deps . \
 && rm /tmp/requirements.txt && uv pip check --system
USER auth
ENV AUTH_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/auth/health')"
CMD ["factory-auth", "serve"]
