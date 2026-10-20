FROM oven/bun:1.3.14@sha256:e10577f0db68676a7024391c6e5cb4b879ebd17188ab750cf10024a6d700e5c4 AS build

WORKDIR /workspace
COPY package.json bun.lock ./
COPY app/package.json app/package.json
RUN bun install --frozen-lockfile --ignore-scripts
COPY app/index.html app/index.html
COPY app/vite.config.js app/vite.config.js
COPY app/public app/public
COPY app/src app/src
RUN bun run --filter @local-k8s-demo/web build

FROM nginx:1.30.6-alpine3.24@sha256:970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5 AS runtime

# Strip vendor startup scripts: they mutate /etc at runtime. Keep served content
# root-owned so the service cannot rewrite it even outside a read-only pod.
RUN addgroup -S -g 10001 web \
    && adduser -S -D -H -u 10001 -G web -s /sbin/nologin web \
    && rm -rf /docker-entrypoint.d /docker-entrypoint.sh \
        /etc/nginx/conf.d /usr/share/nginx/html /var/cache/apk/*

COPY app/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /workspace/app/dist /usr/share/nginx/html

USER 10001:10001
EXPOSE 8080
# The Nginx master reaps workers; QUIT drains active requests.
STOPSIGNAL SIGQUIT
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
