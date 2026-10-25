# syntax=docker/dockerfile:1
# The runtime image is chosen from the pack's approved list (ADR-0045): Docker Hardened first, then
# distroless. The factory's Build station picks one with --build-arg RUNTIME=<stage>: the first that
# scans clean and starts. Keep both stages; Dependabot proposes updates for each.
ARG RUNTIME=runtime-hardened

FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund

FROM node:22-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npx next build

# Approved runtime images (no shell, no package manager, non-root): only the one RUNTIME names is
# pulled and built. Pinned by Node version; `scripts/pin-base-images` in the factory adds digests.
FROM dhi.io/node:22-alpine3.23 AS runtime-hardened
FROM gcr.io/distroless/nodejs22-debian12:nonroot AS runtime-distroless

# Runtime: the standalone server and its static files, as uid 65532 on every approved image (the
# chart's runAsUser). The image's entrypoint is node.
FROM ${RUNTIME}
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=65532:65532 /app/.next/standalone ./
COPY --from=build --chown=65532:65532 /app/.next/static ./.next/static
COPY --from=build --chown=65532:65532 /app/public ./public
USER 65532:65532
EXPOSE 3000
CMD ["server.js"]
