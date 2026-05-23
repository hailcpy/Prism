# Multi-stage production build for Next.js

# Stage 1: Dependencies
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --only=production

# Stage 2: Builder
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

# NEXT_PUBLIC_* env vars are baked into the bundle at build time
ARG NEXT_PUBLIC_CHATBOT_API_URL
ENV NEXT_PUBLIC_CHATBOT_API_URL=$NEXT_PUBLIC_CHATBOT_API_URL

RUN npm run build

# Stage 3: Production runner
# Next.js standalone output contains a minimal server.js with dependencies
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production

# Copy the standalone output
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000

CMD ["node", "server.js"]
