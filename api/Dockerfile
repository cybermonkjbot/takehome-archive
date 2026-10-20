FROM python:3.12.15-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS build

WORKDIR /build
COPY datastore/requirements.lock ./requirements.lock
COPY datastore/__init__.py ./datastore/__init__.py
COPY datastore/postgres.py ./datastore/postgres.py
COPY api/main.py ./main.py
RUN python -m pip install --no-cache-dir --no-compile --only-binary=:all: \
      --require-hashes --target=/opt/python -r /build/requirements.lock

FROM python:3.12.15-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS runtime

# Keep pip, package caches, source trees, and development headers out of runtime.
RUN addgroup -S -g 10001 api \
    && adduser -S -D -H -u 10001 -G api -s /sbin/nologin api \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
        /usr/local/lib/python3.12/site-packages/pip \
        /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
        /usr/local/bin/pip* /usr/local/include /var/cache/apk/*

WORKDIR /srv
COPY --from=build /opt/python /opt/python
COPY --from=build /build/datastore /srv/datastore
COPY --from=build /build/main.py /srv/main.py
ENV PYTHONPATH=/opt/python:/srv PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM
ENTRYPOINT ["python"]
CMD ["main.py"]
