# Upstream image identity

Registry reads on 2026-10-20 returned HTTP 200 and the following `Docker-Content-Digest` values from Docker Hub's Registry v2 manifests endpoint. These are multi-platform OCI index digests, not invented examples. They pin the artifacts used by every `FROM` in this repository. The CloudNativePG operand entry was separately verified against GHCR's Registry v2 manifest endpoint.

| Purpose | Version | OCI index SHA256 |
| --- | --- | --- |
| Web build | `oven/bun:1.3.14` | `e10577f0db68676a7024391c6e5cb4b879ebd17188ab750cf10024a6d700e5c4` |
| Web runtime | `nginx:1.30.6-alpine3.24` | `970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5` |
| Collector build/runtime | `python:3.12.15-alpine3.24` | `1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4` |
| Log API build/runtime | `python:3.12.15-alpine3.24` | `1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4` |
| CloudNativePG PostgreSQL operand | `ghcr.io/cloudnative-pg/postgresql:16.15-minimal-trixie` | `f76ba02680a07bdafaa00c81827351b15e671ac11e0e6a0b72f8ee5835cc82c6` |

Version choice does not imply these are vulnerability-free releases. No final application images have been built or scanned in this change, and no final web/API/log-api/collector digest is supplied. The PostgreSQL operand pin does not verify compatibility with the target CloudNativePG operator. Use a reviewed rebuild/scan/signing process when refreshing pins; do not replace them with floating tags. Validate the selected platform manifests and resulting images for each target architecture.
