# Local Kubernetes system snapshot

## Runtime contract

- **Ports:** the web container listens on HTTP `8080` behind Service port `80`. The snapshot API listens on `8080`, and the log API on `8181`, behind internal-only Services on port `80`.
- **Probes:** the web container uses local `/healthz`; the snapshot API also uses `/readyz` for its PostgreSQL dependency, and the log API checks Kubernetes API access on `/readyz`.
- **Writable mounts:** the Nginx web pod has a 16 MiB memory-backed `/tmp`; the collector Job has an 8 MiB memory-backed `/tmp`. The APIs have no writable filesystem mount. Snapshot API and collector mount only their own database credential files at `/var/run/secrets/datastore`; the log API receives a projected service-account token for its scoped read calls.
- **Infrastructure dependencies:** an HA PostgreSQL service with TLS, a namespace-scoped `SecretStore` from the selected External Secrets provider, cluster DNS and an enforcing CNI, a three-zone Kubernetes target, a metrics API for the HPAs, an approved browser access path with authentication for raw logs, and a target-specific network path from the log API to the Kubernetes API server.

Deployment instructions remain blocked until those platform choices and their schemas, network identities, TLS settings, and capacity are verified. The host and browser name in the inactive contracts are `postgres-cluster-rw.demo.svc.cluster.local` and `app.demo.internal`; verify both against the selected platform before use.

## Data flow

The UTC CronJob manifest is active (`suspend: false`) and schedules a collection every four hours. Each run samples details visible from its own pod: pod name, kernel release, architecture, Python version, logical CPU count, cgroup CPU quota, cgroup memory current/limit, and one-minute load average. Kernel release and load average are node-wide values; the collector does not inspect the host filesystem or other workloads. The browser displays the latest successfully stored sample and its timestamp.

The workload base does not include the datastore ConfigMap, ExternalSecrets, or PostgreSQL network rules. The snapshot API and collector therefore cannot start successfully from the base alone. Do not admit the base until a reviewed deployment overlay supplies the selected database integration and credentials, or deliberately suspends the CronJob in that overlay.

The collector inserts one row per Kubernetes Job name. Database uniqueness makes retries of that Job idempotent. Each successful collection removes rows older than 30 days. The Python API has a read-only database role; the collector has insert/delete privileges. Database schema ownership and credentials stay outside the workloads. See [`datastore/schema.sql`](/Users/joshua/Documents/ChatGPT/Takehome/datastore/schema.sql).

The browser calls `/api/v1/snapshot` and `/api/v1/logs/{component}` on its own origin. Nginx routes snapshots to the snapshot API and logs to the internal component-log Service. Neither API has a public Service endpoint.

The log API uses fixed label selectors and container names for the web app, collector, and datastore. It returns at most 100 lines by default, 200 lines when requested, six pods, and 16 KiB per pod. The datastore result has no matching pods until its integration is installed. Its service account can read pod metadata and logs only in `demo`; a target-specific egress rule to the Kubernetes API server is still required. Raw logs may contain sensitive runtime data, so an authenticated gateway policy is required before exposing the web route.

## Workload protection and scaling

- Web, snapshot API, and log API Deployments start with three replicas and can scale to six at 70% average CPU. Scaling needs a working metrics API and capacity for up to six replicas of each Deployment.
- Required zone spreading uses three real zones and can leave replacement pods pending when a zone is absent. Each Deployment has a PDB retaining two available replicas during voluntary disruption; neither setting proves node/zone recovery.
- Network policies start from namespace-wide ingress and egress denial. DNS access is limited to selected CoreDNS pods; web-to-API traffic is limited to the API ports. The log API has DNS egress but no Kubernetes API-server allow rule yet. Exact database egress is also absent until the datastore and CNI identities are confirmed.
- Pods use UID/GID 10001, RuntimeDefault seccomp, read-only roots, and dropped capabilities. Only the log API mounts a service-account token, with a namespaced read Role. App and APIs allow endpoint propagation and request draining. The finite CronJob has no service probes or PDB.

CPU autoscaling is a configuration example, not measured capacity evidence. The API bounds concurrent request threads and uses short PostgreSQL connection and statement timeouts. Representative load, database connection capacity, HPA reaction, graceful drain, and node/zone recovery still need validation on the selected platform.

## Source map

- `app/`: Vite UI, static Nginx runtime, and same-origin API proxy.
- `api/`: snapshot HTTP API and its hardened container build.
- `log-api/`: bounded read-only pod-log API and its hardened container build.
- `collector/`: finite scheduled collector and its hardened container build.
- `datastore/`: shared PostgreSQL access, hash-locked Python dependencies, and schema/grants.
- `k8s/bootstrap/`: namespace Restricted Pod Security Admission and default-deny policy.
- `k8s/`: application and collector workloads, Services, scaling, budgets, and workload network rules.
- `k8s/integrations/`: inactive Gateway and External Secrets contracts.
- `scripts/render-k8s.mjs`: offline render requiring exact published image references.
- `docs/security-review.md`: release blockers and checks not run.
- `docs/ai-assistance.md`: prompt and tool disclosure for the take-home.

The original standalone PostgreSQL StatefulSet remains quarantined at `datastore/legacy-statefulset.yaml.disabled`. Do not deploy or scale it as an HA database.

## Build artifacts

The repository uses Bun 1.3.14 and Docker BuildKit. Each image builds from the repository root, and each Dockerfile has a deny-by-default context allowlist. No dependency installation or image build has been run for this task.

```sh
bun run build
bun run image:web
bun run image:api
bun run image:log-api
bun run image:collector
```

For local snapshots, first configure a PostgreSQL database, apply `datastore/schema.sql` through its approved migration process, and set the `DATABASE_*` variables and credential file paths described in `datastore/postgres.py`. Start the API with `API_HOST=127.0.0.1 bun run dev:api`. Vite proxies `/api/` to that API on port 8080 by default; override it with `SNAPSHOT_API_PROXY_TARGET` when needed.

For local logs, run `kubectl proxy --address=127.0.0.1 --port=8001`, then `KUBERNETES_API_URL=http://127.0.0.1:8001 bun run dev:log-api`, then `bun run dev`. The local log API uses the identity and RBAC of `kubectl proxy`; it requires `list pods` and `get pods/log` in `demo`. Vite proxies `/api/v1/logs` to port 8181 by default; override it with `LOG_API_PROXY_TARGET`. The UI, snapshot API, and log API bind to loopback during development.

The Dockerfiles pin exact base versions and verified SHA256 digests. API and collector install only hash-locked Psycopg wheels in their build stages. Local image tags are not release identities. A release requires published image digests, artifact review, signature/provenance checks, and a selected platform. `release.example.json` lists all required image inputs.

The renderer is read-only and does not contact or apply to a cluster. Once published images exist, provide a local ignored `release.json` with `webImage`, `apiImage`, `logApiImage`, and `collectorImage`, each using `registry/repository:major.minor.patch@sha256:<64 lowercase hex characters>`, then use:

```sh
bun run k8s:render release.json
```

The render command validates immutable image syntax and unresolved placeholders. It does not validate registries, signatures, Kubernetes schemas, network-policy enforcement, or readiness for deployment. No apply script is provided.

## Remaining platform decisions

Before writing deployment steps or admitting workloads, select and verify:

1. Kubernetes distribution/version, real node and zone topology, CNI policy behavior, CoreDNS labels, the exact API-server egress destination for the log API, metrics API, and capacity for rollouts plus HPA maxima.
2. HA PostgreSQL operator/service, TLS certificate identity, database roles, backup and restore process, and approved schema migration procedure.
3. External Secrets provider and namespaced `SecretStore`; create separate versioned API-reader and collector-writer credentials.
4. Gateway controller identity, HTTPS listener/certificate, an authentication policy for raw log access, DNS for `app.demo.internal`, and local browser access procedure.
5. Published image repositories, digests, architectures, signature/provenance policy, and final image scans.

Then validate API/operator schemas, secret rotation, exact network allowlists, read-only startup, scheduling, HPA behavior, request draining, collector retries/idempotency, and database failover/restore. Until those decisions and checks are complete, keep deployment blocked. The collector manifest is currently unsuspended but depends on the inactive datastore integration; the browser route and database integration files remain outside the workload base.
