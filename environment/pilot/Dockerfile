FROM node:22-alpine AS builder
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable
WORKDIR /usr/src/app
ARG ARCHITECTURE
ARG SERVICE
COPY implementation/${ARCHITECTURE}/${SERVICE}-service/ ./
# Apply the same explicit dependency build policy as the baseline.
COPY environment/pilot/pnpm-workspace.yaml ./pnpm-workspace.yaml
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile
# Reuse the exact baseline observer; do not create another database pool.
COPY implementation/distributed-baseline/order-service/src/diagnostics/ /tmp/diagnostics/
RUN if [ "$SERVICE" = order ] && [ "$ARCHITECTURE" != distributed-baseline ]; then \
      cp -R /tmp/diagnostics ./src/diagnostics; \
    fi
RUN pnpm build
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm prune --prod

FROM node:22-alpine
WORKDIR /usr/src/app
ENV NODE_ENV=production
COPY --chown=node:node --from=builder /usr/src/app/package.json ./
COPY --chown=node:node --from=builder /usr/src/app/node_modules ./node_modules
COPY --chown=node:node --from=builder /usr/src/app/dist ./dist
COPY --chown=node:node environment/pilot/register-diagnostics.cjs ./pilot/register-diagnostics.cjs
USER node
CMD ["node", "--require", "./pilot/register-diagnostics.cjs", "dist/main.js"]
