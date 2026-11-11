FROM node:22-bookworm-slim@sha256:18052396bf1d0c6e0a8c58181b5c1457da19409efbd5032a3f4d86974e74d13c AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build
FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/nginx.conf
USER 101:101
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
