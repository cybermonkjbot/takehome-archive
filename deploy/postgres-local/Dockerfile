# Preserve the registry-verified CNPG PostgreSQL runtime and binaries. The
# operator requires UID/GID 10001, so only the postgres identity records change.
FROM ghcr.io/cloudnative-pg/postgresql:16.15-minimal-trixie@sha256:c844ed148b640b912fe5e5163558e01327249a0f22edf163cb962fd657485a27 AS upstream

# This pinned Alpine stage only edits passwd/group metadata; no builder
# tools or package cache are copied into the runtime image.
FROM python:3.12.15-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS identity
COPY --from=upstream /etc/passwd /tmp/passwd
COPY --from=upstream /etc/group /tmp/group
RUN set -eu; \
    test "$(awk -F: '$1 == "postgres" { n++ } END { print n + 0 }' /tmp/passwd)" -eq 1; \
    test "$(awk -F: '$1 == "postgres" { n++ } END { print n + 0 }' /tmp/group)" -eq 1; \
    if awk -F: '$3 == 10001 && $1 != "postgres" { found = 1 } END { exit !found }' /tmp/passwd; then exit 1; fi; \
    if awk -F: '$3 == 10001 && $1 != "postgres" { found = 1 } END { exit !found }' /tmp/group; then exit 1; fi; \
    awk -F: 'BEGIN { OFS = ":" } $1 == "postgres" { $3 = 10001; $4 = 10001; $7 = "" } { print }' /tmp/passwd > /tmp/passwd.uid10001; \
    awk -F: 'BEGIN { OFS = ":" } $1 == "postgres" { $3 = 10001 } { print }' /tmp/group > /tmp/group.gid10001

# Retain the upstream runtime contents and metadata, changing only its default
# user to match the fixed identity configured in the Kubernetes pod.
FROM ghcr.io/cloudnative-pg/postgresql:16.15-minimal-trixie@sha256:c844ed148b640b912fe5e5163558e01327249a0f22edf163cb962fd657485a27 AS runtime
COPY --from=identity /tmp/passwd.uid10001 /etc/passwd
COPY --from=identity /tmp/group.gid10001 /etc/group
USER 10001:10001
