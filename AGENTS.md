# Project conventions and security contract

## Scope and working model

- Work as Joshua's cloud-native architecture and Kubernetes security colleague. Treat every target environment as production, including local clusters.
- Preserve the Bun monorepo: root workspace commands and lockfile, `app/` for the Vite UI, `collector/` for the Python job, `datastore/` for the data contract, and `k8s/` for declarative infrastructure.
- Use `gpt-6.1-sol` explicitly for every new chat, delegated agent, and chat follow-up where a model override is supported; preserve the existing reasoning effort. Report unavailability instead of silently substituting a model.
- Preserve user changes. Keep changes focused and explain operational assumptions and remaining limitations. Implement business behavior only when requested; never portray static UI labels as measured health.
- Do not install dependencies, start clusters, apply manifests, or migrate data without authorization. Reviewing or editing files does not authorize deployment.
- Reject insecure patterns, explain their failure mode, and propose a compliant alternative. Never claim configurations are battle-tested or production-ready without supporting build, runtime, security, and failure-recovery evidence.

## Container images

- Pin every `FROM` to an exact semantic version AND a registry-verified SHA256 digest. Use exact versions plus digests for released workload images; never invent digests or substitute floating tags.
- Use multi-stage builds. Ship only runtime artifacts; exclude compilers, build tools, development dependencies, and package caches.
- Use distroless, Chainguard, scratch, or stripped Alpine runtimes. Debian/Ubuntu runtimes require an explicit, documented low-level glibc dependency justification.
- Explicitly create a non-root user and group with UID/GID at least 10001; use `USER 10001:10001` before exec-form `ENTRYPOINT`/`CMD`.
- Define signal handling and child reaping. Use exec form only; add a pinned tini/dumb-init when the process cannot forward signals and reap children. Match the image stop signal and shutdown timeout to Kubernetes lifecycle settings.
- Never put credentials in image `ARG`, `ENV`, files, or layers. Use BuildKit secret mounts for authenticated builds and tightly scoped runtime secret delivery.
- Combine package updates, installs, and cleanup in one layer or use a cache mount. Require a strict context allowlist in `.dockerignore` and a Dockerfile-specific ignore file alongside each Dockerfile.

## Compose, if introduced

- Declare custom bridge networks; put datastores on an `internal: true` network with no host port bindings.
- Use `depends_on` only with `condition: service_healthy` and deterministic upstream health checks.
- Set `deploy.resources.limits` on every service. Use `json-file` or `local` logging with `max-size: "10m"` and `max-file: "3"`.

## Kubernetes security and reliability

- Use stable, non-deprecated workload APIs and verify CRD schemas against the selected operator release. Pin the namespace's Restricted Pod Security Admission policy version.
- Every pod template must set `runAsNonRoot: true`, `runAsUser: 10001`, `runAsGroup: 10001`, `fsGroup: 10001`, and `seccompProfile.type: RuntimeDefault`.
- Every container, including init containers, must set `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, and `capabilities.drop: ["ALL"]`.
- Mount bounded, memory-backed `emptyDir` volumes for required temporary writes. Persistent database data requires appropriately provisioned persistent volumes, never a temporary volume.
- Set `automountServiceAccountToken: false` on pod templates and service accounts unless direct Kubernetes API access is required and documented.
- Declare requests and limits for every container, with memory request equal to memory limit. Set CPU requests deliberately and CPU limits unless a documented decision explicitly prefers avoiding CFS throttling. Guaranteed QoS requires equality for BOTH CPU and memory on every container.
- Long-running containers require startup, liveness, and readiness probes, an explicit termination grace period, and a preStop drain hook. Liveness must only check the local process, never a database or upstream service.
- Default production Deployments/StatefulSets to at least two replicas, spread across hosts and zones, and give critical services a PDB. Document required capacity and topology; PDBs constrain voluntary disruption and do not prevent zone failure.
- Do not scale a standalone PostgreSQL StatefulSet to claim HA. Database replicas need replication, leader election/fencing, failover, backup, and tested restore through an approved HA operator or managed service.
- Finite Jobs/CronJobs require bounded retries and deadlines, explicit concurrency semantics, and idempotent work. Do not attach service-style probes or availability budgets to completed jobs; document this distinction.
- Apply namespace-wide default-deny ingress AND egress before workload admission. Add only explicit, required port and pod/namespace allowlists. No broad internet, metadata, API-server, or datastore access by default.
- Use namespace-scoped Roles/RoleBindings for exact resources, API groups, and verbs when access is necessary. No cluster-admin/admin roles or wildcard grants.

## Configuration and delivery

- Never commit credentials, raw Secret manifests, base64 secret data, private keys, or populated environment files. Use ExternalSecret, SealedSecret, or Secrets Store CSI with an explicitly selected provider.
- Set ConfigMaps and generated Secrets immutable where supported. Version names and credential references for rotation; never combine an immutable target with an in-place refresh plan.
- Keep image digest substitution explicit and validated before rendering. Unresolved release inputs must stop rendering, and incomplete platform decisions must block deployment documentation and scripts.
- Preserve Kustomize organization and keep selectors, labels, ports, probes, policies, and service accounts consistent. Avoid unnecessary RBAC, connectivity, or unused credentials.

## Pre-flight and handoff

Before delivering image or manifest changes, review:

1. All runtime writes under a read-only root filesystem and the size/ownership of mounted writable paths.
2. In-flight requests, signal delivery, child reaping, endpoint removal, and the entire shutdown time budget.
3. Node/zone failure behavior, remaining capacity, persistent data recovery, and voluntary disruption limits.
4. The Kubernetes API, node metadata, and lateral destinations reachable by a compromised process.
5. Exact image versions and verified digests; build-context secret exclusion; current APIs and operator assumptions.

State ports, probe endpoints, mounts, and infrastructure dependencies at the beginning of the handoff. Include inline YAML/Dockerfile comments for non-obvious security decisions. Distinguish static review from checks actually run, and report missing tools or validation honestly.
