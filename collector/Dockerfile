FROM python:3.12.15-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS build

WORKDIR /build
COPY datastore/requirements.lock ./requirements.lock
COPY datastore/__init__.py ./datastore/__init__.py
COPY datastore/postgres.py ./datastore/postgres.py
COPY collector/main.py ./main.py
RUN python -m pip install --no-cache-dir --no-compile --only-binary=:all: \
      --require-hashes --target=/opt/python -r /build/requirements.lock \
    && python -m compileall -q /build/main.py /build/datastore

FROM python:3.12.15-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS runtime

# pip is used only in the build stage; only pinned runtime wheels are copied in.
RUN addgroup -S -g 10001 collector \
    && adduser -S -D -H -u 10001 -G collector -s /sbin/nologin collector \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
        /usr/local/lib/python3.12/site-packages/pip \
        /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
        /usr/local/bin/pip* /usr/local/include /var/cache/apk/*

WORKDIR /collector
COPY --from=build /opt/python /opt/python
COPY --from=build /build/datastore /collector/datastore
COPY --from=build /build/main.py ./main.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PYTHONPATH=/opt/python:/collector

USER 10001:10001
STOPSIGNAL SIGTERM
# Single process with explicit SIGTERM handling. Add a pinned init/reaper before
# introducing subprocesses or multiprocessing.
ENTRYPOINT ["python"]
CMD ["main.py"]
