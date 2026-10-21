FROM scratch AS web-artifacts
COPY app/dist/ /dist/

FROM nginx:1.30.6-alpine3.24@sha256:970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5 AS runtime

# Strip vendor startup scripts: they mutate /etc at runtime. Keep served content
# root-owned so the service cannot rewrite it even outside a read-only pod.
RUN addgroup -S -g 10001 web \
    && adduser -S -D -H -u 10001 -G web -s /sbin/nologin web \
    && rm -rf /docker-entrypoint.d /docker-entrypoint.sh \
        /etc/nginx/conf.d /usr/share/nginx/html /var/cache/apk/*

COPY app/nginx.conf /etc/nginx/nginx.conf
COPY --from=web-artifacts /dist/ /usr/share/nginx/html/

USER 10001:10001
EXPOSE 8443
# The Nginx master reaps workers; QUIT drains active requests.
STOPSIGNAL SIGQUIT
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
