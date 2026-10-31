FROM node:24.22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Unprivileged image: runs as non-root and listens on 8080.
FROM nginxinc/nginx-unprivileged:1.30-alpine
# The base image lags Alpine's security fixes; CI scans the result.
USER root
RUN apk upgrade --no-cache
USER 101
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
