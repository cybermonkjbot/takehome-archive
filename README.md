# Local Kubernetes system snapshot

## Runtime contract

- **Ports:** the web container listens on HTTP `8080` behind Service port `80`. The snapshot API listens on `8080`, and the log API on `8181`, behind internal-only Services on port `80`. The CloudNativePG datastore listens on port `5432` with instance manager on `8000`.
- **Probes:** the web, snapshot API, and log API expose local `/healthz`; the snapshot API uses `/readyz` for PostgreSQL, and the log API uses `/readyz` to check Kubernetes API access. The log API remains unready while deployment egress is DNS-only.
- **Writable mounts:** the Nginx web pod has a 16 MiB memory-backed `/tmp`; the collector Job has an 8 MiB memory-backed `/tmp`. The APIs have no writable filesystem mount. Snapshot API and collector mount only their own database credential files at `/var/run/secrets/datastore`; the log API receives a projected service-account token for its scoped read calls.
- **Infrastructure dependencies:** a compatible CloudNativePG operator and persistent storage, a namespace-scoped `SecretStore` from the selected External Secrets provider, cluster DNS and an enforcing CNI, three failure zones, and a metrics API for the HPAs. The raw-log route remains disabled until an identity-verifying integration is implemented and Kubernetes API access is verified.

The manifests name CloudNativePG and `postgres-cluster-rw.demo.svc.cluster.local`; they have not been checked against a target cluster. The operator, SecretStore provider, storage and backup services, API-server network path, and image digests need target values before admission. No browser ingress is configured.

## Data flow

The UTC CronJob manifest is active (`suspend: false`) and schedules a collection every four hours. Each run samples details visible from its own pod: pod name, kernel release, architecture, Python version, logical CPU count, cgroup CPU quota, cgroup memory current/limit, and one-minute load average. Kernel release and load average are node-wide values; the collector does not inspect the host filesystem or other workloads. The browser displays the latest successfully stored sample and its timestamp.

The Kustomize file contains the immutable datastore ConfigMap, ExternalSecrets, a three-instance CloudNativePG Cluster, and datastore network policies. The PostgreSQL operand image is pinned. Before deployment, install and verify a compatible operator, provision the namespace `SecretStore`, deliver the database CA, select a storage class, configure backups, and test restore. Until then the database is not usable or recoverable.

The collector inserts one row per Kubernetes Job name. Database uniqueness makes retries of that Job idempotent. Each successful collection removes rows older than 30 days. The Python API has a read-only database role; the collector has insert/delete privileges. Database schema ownership and credentials stay outside the workloads. See [`datastore/schema.sql`](/Users/joshua/Documents/ChatGPT/Takehome/datastore/schema.sql).

The browser calls `/api/v1/snapshot` and `/api/v1/logs/{component}` on its own origin. Nginx routes snapshots to the internal snapshot API. The Nginx log route returns `503` until the selected identity-verifying integration is implemented and verified. Neither API has a public Service endpoint. Host browser access uses a port-forward to the internal web Service.

After the workloads are installed in a local cluster, a normal browser on the host can reach the web Service with `kubectl -n demo port-forward service/web-app 8080:80`, then open `http://127.0.0.1:8080`. This keeps the Service internal and relies on the caller's Kubernetes credentials. The in-cluster log route remains unavailable until user authentication and API-server egress are configured.

The log API uses fixed label selectors and container names for the web app, collector, and datastore. It returns at most 100 lines by default, 200 lines when requested, six pods, and 16 KiB per pod. Its service account has a narrow Role to read pod metadata and logs only in `demo`. The current network policy grants DNS only; Kubernetes API egress stays closed until the target control-plane path and CNI behavior are verified.

Raw logs can contain sensitive runtime data, stack traces, or configuration fragments. Header presence does not verify user identity. A real OIDC/JWT or external-auth integration needs a selected provider, verified Gateway schema, and a trusted request path; until that integration is implemented Nginx returns `503` for the raw-log path.

## Workload protection and scaling

- Web, snapshot API, and log API Deployments start with three replicas and can scale to six at 70% average CPU. Scaling needs a working metrics API and capacity for up to six replicas of each Deployment.
- Required zone spreading uses three real zones and can leave replacement pods pending when a zone is absent. Each Deployment has a PDB retaining two available replicas during voluntary disruption; neither setting proves node/zone recovery.
- Network policies start from namespace-wide ingress and egress denial. DNS access is limited to selected CoreDNS pods; web-to-API traffic is limited to the API ports. The log API currently has DNS egress only. Datastore access rules require validation against the selected operator labels and CNI.
- Pods use UID/GID 10001, RuntimeDefault seccomp, read-only roots, and dropped capabilities. Only the log API mounts a service-account token, with a namespaced read Role. App and APIs allow endpoint propagation and request draining. The finite CronJob has no service probes or PDB.

CPU autoscaling settings are configuration, not measured capacity evidence. The API bounds concurrent request threads and uses short PostgreSQL connection and statement timeouts. Representative load, database connection capacity, HPA reaction, graceful drain, and node/zone recovery still need validation on the selected platform.

## Source map

- `app/`: Vite UI, static Nginx runtime, and same-origin API proxy; raw logs fail closed until Gateway authentication is configured.
- `api/`: snapshot HTTP API and its hardened container build.
- `log-api/`: bounded read-only pod-log API and its hardened container build.
- `collector/`: finite scheduled collector and its hardened container build.
- `datastore/`: shared PostgreSQL access, hash-locked Python dependencies, and schema/grants.
- `k8s/bootstrap/`: namespace Restricted Pod Security Admission and default-deny policy.
- `k8s/`: application and collector workloads, Services, scaling, budgets, and workload network rules.
- `k8s/integrations/`: CloudNativePG, External Secrets, and datastore-network resources that require target-specific operators, providers, storage, and CNI behavior.
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

The renderer requires an explicit release-input file with published immutable image references for the web app, snapshot API, log API, and collector. It validates those inputs and the statically pinned CloudNativePG image; it does not contact or apply to a cluster. No application image has been built or published for this workspace.

Once image references are available, use:

```sh
bun run k8s:render release.json
```

The render command validates immutable image syntax and rejects unresolved image references. It does not validate registries, signatures, Kubernetes schemas, network-policy enforcement, or readiness for deployment. No apply script is provided.

## Remaining platform decisions

Before deploying workloads, select and verify:

1. Target cluster/context, Kubernetes and CNI versions, actual API-server destination/service CIDR, CoreDNS labels, three-zone topology, metrics API, and capacity.
2. CloudNativePG operator release and CRD, digest-pinned compatible PostgreSQL image, storage class, TLS CA/identity, database roles, object-store backup target, and tested restore process.
3. External Secrets provider and namespace `SecretStore`; provision versioned bootstrap-owner, API-reader, and collector-writer entries. The bootstrap username must match `initdb.owner`; managed role usernames must match `snapshot_api` and `snapshot_collector`.
4. Gateway implementation/version, HTTPS listener/certificate, DNS for `app.demo.internal`, and OIDC/JWT issuer, audience, JWKS/client-secret reference, and application integration for raw logs.
5. Published web/API/log-api/collector image references and digests, registry access, architectures, signature/provenance policy, and final image scans.

Then validate API/operator schemas, secret rotation, exact network allowlists, read-only startup, scheduling, HPA behavior, request draining, collector retries/idempotency, and database failover/restore. Until those decisions and checks are complete, keep deployment blocked.
