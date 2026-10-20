# Security and delivery review

Reviewed 2026-10-20. Scope: repository files only. No dependencies were installed, no images were built, and no cluster was started or changed during this handoff.

## Implemented behavior

- A Kubernetes CronJob runs every four hours, records pod-visible system data in PostgreSQL, and uses the Job name as an idempotency key.
- The web application displays the newest successful sample and collection time. A host browser can access its internal Service through `kubectl -n demo port-forward service/web-app 8080:80`.
- Web, snapshot API, and log API Deployments have three replicas, CPU-based HPAs up to six, topology spread, and PDBs. HPA operation requires a metrics API and adequate capacity.
- Workload manifests set UID/GID 10001, read-only root filesystems, dropped capabilities, RuntimeDefault seccomp, and bounded memory-backed temporary mounts where needed.
- Namespace network policy starts from default deny. Workload and datastore paths are allowlisted by port and selectors. Enforcement depends on the target CNI.
- The log API has a namespace-scoped read-only Role and bounded responses. The raw-log route returns HTTP 503 until user authentication is configured. Its egress policy allows DNS only, so it cannot currently reach the Kubernetes API from an installed workload.

## Delivery blockers

1. **Published workload images:** no application image has been built or published. The ignored local `release.json` was found reusing the Nginx and Python base-image digests for workload image references. The renderer now rejects Dockerfile base-image digests. Replace these values with actual published image digests before rendering.
2. **Database platform:** the manifests require a compatible CloudNativePG operator and CRD, provisioned storage, an External Secrets `SecretStore`, database CA and versioned role credentials, object-store backups, and a tested restore.
3. **Cluster network:** confirm the Kubernetes and CNI versions, CoreDNS labels, control-plane API endpoint, three-zone topology, metrics API, capacity, and datastore selectors.
4. **Raw-log identity:** select and implement a real OIDC/JWT or external-auth integration before exposing the raw-log route. Header presence is not authentication.
5. **Runtime evidence:** build and scan the exact images; verify startup on a read-only filesystem, shutdown/drain timing, collector retry behavior, HPA capacity, zone disruption, database failover, and restore on the selected target.

The Kustomize render command is offline and does not apply resources. It checks immutable image syntax and rejects unresolved references; it does not prove registry ownership, signatures, CRD compatibility, CNI enforcement, or cluster readiness.

## Verification record

The current frontend production build completed with `bun run build`. No tests, dependency installation, image build, or cluster operation was run. Docker and kubectl are unavailable in the local environment; container, registry, Kustomize, and cluster checks were not run. Earlier Python and YAML checks predate the latest changes and are not evidence for the current tree.
