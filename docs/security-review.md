# Security and reliability review

Reviewed on 2026-10-20. Scope: repository files only. No dependency installation, image build, cluster startup, deployment, secret creation, or data migration was performed.

## Confirmed findings and changes

| Finding in the previous scaffold | Repository action |
| --- | --- |
| Root-context app build copied `nginx.conf` from the wrong path | Corrected to `COPY app/nginx.conf` |
| Floating Nginx/Python base tags and no immutable base references | Pinned exact versions and registry-confirmed OCI index digests |
| Collector shipped a Debian Python runtime with an installer | Multi-stage Alpine runtime with pip and development headers removed |
| Numeric container user was not explicitly created | Added named service users/groups at UID/GID 10001 |
| Vendor Nginx startup scripts attempted mutable configuration | Removed scripts and used direct exec-form Nginx startup |
| Broad build contexts could include unrelated files | Added root and Dockerfile-specific source allowlists |
| Pod templates lacked Restricted security settings | Added pod/container contexts, read-only roots, bounded memory-backed temporary volumes, and disabled token mounting |
| No network isolation | Added separate namespace bootstrap with deny-all ingress/egress; no outbound grants for unused integrations |
| Single web replica, no disruption budget or topology requirements | Three replicas, three-zone/host spread, PDB, controlled rollout, HPA floor of three |
| Missing startup/drain behavior | Added startup probe, explicit probe timing, five-second preStop delay, SIGQUIT image stop, 25-second worker drain, 45-second termination budget |
| Unbounded/default collector restart behavior | UTC schedule, no overlap, bounded retries/deadline, explicit signal handling; the current CronJob is unsuspended and requires its inactive datastore integration before it can run successfully |
| Raw Secret creation was recommended | Removed that instruction; added an inactive ExternalSecret integration contract |
| NodePort opened the app without a defined edge/TLS policy | Removed active NodePort; provided an inactive Gateway API contract and matching allowlist |
| Single PostgreSQL process was the only datastore | Quarantined its unchanged legacy manifest; excluded it from rendering pending an HA platform decision |
| A one-command apply could bypass incomplete infrastructure decisions | Removed the apply script; added render-only immutable image inputs |

## Release blockers and required decisions

1. **Database platform:** choose an approved HA operator or managed PostgreSQL. A multi-replica StatefulSet alone supplies no database replication or leader election. An operator changes CRDs, RBAC, image compatibility, lifecycle ownership, storage, networking, and backup configuration; those cannot be safely guessed. The legacy file is retained only for review and is not part of the secured workload base.
2. **Infrastructure:** establish Kubernetes/CNI versions, real node/zone topology and spare capacity, Gateway implementation/identity, TLS/DNS ownership, secret-store identity, and storage/backup services. A single-node environment cannot demonstrate zone survival under this policy.
3. **Artifact evidence:** build/publish the app, snapshot API, log API, and collector; obtain real digests, validate platform architectures, verify signatures/provenance, scan the final artifacts, and record the results. Registry lookup established identity of base artifacts only.
4. **Runtime evidence:** run containers with the declared UID, dropped capabilities, seccomp, read-only root and bounded tmpfs. Confirm `/healthz`, CSP compatibility, no unexpected writes, inherited dependency absence, and actual CRI stop-signal behavior. Confirm the complete request-drain sequence fits inside the termination budget.
5. **Policy and recovery evidence:** validate schemas/admission, establish deny-all before pods, prove only the intended gateway can connect, and prove API/metadata/lateral egress is denied. Exercise rollout, node drain, zone outage, job cancellation/retry, and datastore failover/restore after selecting that platform.
6. **Sizing:** initial resource bounds have not been profiled. Prove peak RSS including tmpfs, CPU throttling behavior, survivor capacity, and HPA behavior; then update budgets using measurements. A PDB controls voluntary eviction, not infrastructure failure, and a replica count is not a measured availability guarantee.

The hardened workload base can be rendered for review after providing image references. It intentionally does not deploy a datastore or publish an edge endpoint. The snapshot API and CronJob both reference datastore resources that are in inactive integrations; do not admit the base until an overlay supplies those prerequisites, including database network policy, or deliberately suspends the CronJob. The log reader has a narrow namespace-scoped read Role, but no control-plane egress allowlist; it cannot reach the Kubernetes API until the target-specific destination is established. Raw logs also require a selected authentication policy before the browser route is exposed. This does not claim end-to-end production readiness. Browser and data integration contracts under `k8s/integrations/` remain inactive until their platform inputs are selected and verified.

## Component log reader

The UI reads `/api/v1/logs/{component}` through the same origin. `log-api/` maps the three allowed component IDs to fixed selectors/container names, caps each request at 200 lines, six pods, and 16 KiB per pod, and renders returned content as text. Its service account can list pods and read `pods/log` in the `demo` namespace. The token is used only by the server-side API and is never included in browser assets.

The active default-deny egress policy allows the log API to use cluster DNS but has no Kubernetes API-server allow rule. Its `/readyz` probe therefore stays unready until a narrow, target-specific control-plane rule is added; the cluster service CIDR/API endpoint is unknown, so no broad egress rule is included. The Gateway route is also inactive. Raw logs can contain secrets or personal data, so an authentication policy is required before exposing the app route.

Local log development can use `kubectl proxy` bound to loopback and `KUBERNETES_API_URL=http://127.0.0.1:8001 bun run dev:log-api`; this uses the local kubeconfig identity. Snapshot development requires a configured PostgreSQL endpoint and credentials. The Vite config proxies snapshot requests to port 8080 and log requests to port 8181 by default.

## Recorded checks and finalization status

The checks below were recorded during an earlier review pass. They were not rerun after the latest integration edits. During finalization, files were reviewed and documentation/integration fixes were made; no tests, builds, image builds, or cluster operations were run.

- Parsed 13 YAML files (17 documents) with the installed Bun 1.3.14 YAML parser, including the inactive integration contracts; checked every declared Kustomize resource path exists.
- Checked selected security/resource/lifecycle/topology fields in the pod templates reviewed in that pass, namespace default-deny policy, service-account token settings, HPA/PDB replica alignment, and immutable configuration/ExternalSecret fields.
- Checked every Dockerfile `FROM` for an exact semantic version and 64-character SHA256 pin, multi-stage structure, explicit UID, Dockerfile-specific ignore file, and exec-form command syntax.
- Checked collector Python syntax and renderer JavaScript syntax. Checked renderer refusal behavior for missing release input, the unresolved example, and an unsupported apply flag; these paths exit unsuccessfully without producing manifest output.
- Checked the Kustomize loader and image transformer source: temporary overlays must be siblings of the base (ancestor imports are rejected), and setting both `newTag` and `digest` preserves the version alongside the digest.

The earlier review recorded Docker and kubectl as unavailable. No image build, full Kustomize render, Kubernetes schema/admission check, container runtime check, or failure-injection exercise has run. These static checks do not establish the release evidence listed above.

## Schema and lifecycle sources consulted

- Kubernetes Pod lifecycle and container stop signals: `https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/`
- Kubernetes Pod Security Standards: `https://kubernetes.io/docs/concepts/security/pod-security-standards/`
- Kubernetes topology spread constraints: `https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/`
- Kubernetes NetworkPolicy semantics: `https://kubernetes.io/docs/concepts/services-networking/network-policies/`
- External Secrets v1 API definitions: `https://github.com/external-secrets/external-secrets/blob/main/apis/externalsecrets/v1/externalsecret_types.go`
- CloudNativePG security and operator boundaries, considered as a possible HA alternative: `https://github.com/cloudnative-pg/cloudnative-pg/blob/main/docs/src/security.md`
- Kustomize loader: `https://github.com/kubernetes-sigs/kustomize/blob/master/api/internal/loader/fileloader.go`
- Kustomize image tag/digest transformer: `https://github.com/kubernetes-sigs/kustomize/blob/master/api/filters/imagetag/updater.go`

External Secrets declares `immutable` under `spec.target`, not `spec.target.template`. The contract uses the actual field location. The operator version is a platform prerequisite and must be pinned and checked against its installed CRDs before activation.
