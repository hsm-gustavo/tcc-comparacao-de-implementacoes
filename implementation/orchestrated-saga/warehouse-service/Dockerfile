# base, instala pnpm
FROM node:22-alpine AS base
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable

WORKDIR /usr/src/app

# deps, instala todas as deps
FROM base AS deps
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

# builder, compila
FROM base AS builder
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml ./
COPY --from=deps /usr/src/app/node_modules ./node_modules
COPY . .
RUN pnpm build
# filtra as deps para producao
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm prune --prod

# runner, imagem final
FROM node:22-alpine AS runner
WORKDIR /usr/src/app

ENV NODE_ENV=production

USER node

COPY --chown=node:node package.json ./
COPY --chown=node:node --from=builder /usr/src/app/node_modules ./node_modules
COPY --chown=node:node --from=builder /usr/src/app/dist ./dist

EXPOSE 3002

CMD ["node", "dist/main.js"]
