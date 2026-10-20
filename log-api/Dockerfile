FROM python:3.12.15-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS build

WORKDIR /build
COPY log-api/main.py ./main.py
RUN python -m compileall -q /build/main.py

FROM python:3.12.15-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS runtime

RUN addgroup -S -g 10001 logs \
    && adduser -S -D -H -u 10001 -G logs -s /sbin/nologin logs \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
        /usr/local/lib/python3.12/site-packages/pip \
        /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
        /usr/local/bin/pip* /usr/local/include /var/cache/apk/*

WORKDIR /log-api
COPY --from=build /build/main.py ./main.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

USER 10001:10001
EXPOSE 8181
STOPSIGNAL SIGTERM
ENTRYPOINT ["python"]
CMD ["main.py"]
