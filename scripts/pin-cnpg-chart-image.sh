#!/bin/sh
set -eu

expected='ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1'
pinned='ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1@sha256:9a10dd7d7466310d5d543f618478cb905093cfc7a5463b538f449da626660083'
manifest=$(cat)

case "$manifest" in
  *"$expected"*) ;;
  *) echo "CNPG chart render did not contain the expected operator image $expected" >&2; exit 1 ;;
esac

printf '%s\n' "$manifest" | sed "s|$expected|$pinned|g"
