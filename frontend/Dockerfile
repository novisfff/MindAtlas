# ============================================
# MindAtlas Frontend Dockerfile
# Multi-stage build: Node.js builder + Nginx
# ============================================

# Stage 1: Builder
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies (cached layer)
COPY package.json package-lock.json* ./
RUN npm ci

# Build application
COPY . .
RUN npm run build
RUN node scripts/compute-build-content-digest.mjs dist > /tmp/frontend-build-content-sha256

# Stage 2: Production
FROM nginx:alpine

ARG APP_BUILD_REVISION=development
ARG FRONTEND_BUILD_CONTENT_DIGEST=unrecorded

COPY --from=builder /tmp/frontend-build-content-sha256 /tmp/frontend-build-content-sha256
RUN test "$FRONTEND_BUILD_CONTENT_DIGEST" != "unrecorded" && \
    test "$FRONTEND_BUILD_CONTENT_DIGEST" = "$(cat /tmp/frontend-build-content-sha256)"

LABEL org.opencontainers.image.revision=${APP_BUILD_REVISION} \
      io.mindatlas.platform="linux/amd64" \
      io.mindatlas.frontend-build-content-sha256=${FRONTEND_BUILD_CONTENT_DIGEST}

# Copy built assets
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
