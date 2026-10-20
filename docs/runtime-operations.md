# Runtime operations

## Service contract

| Workload | Container port / Service port | Health endpoints | Writable mount | Kubernetes API access |
| :--- | :--- | :--- | :--- | :--- |
| `web-app` | TCP 8080 / ClusterIP 80 | `/healthz` | 16 MiB memory-backed `/tmp` | No service-account token |
| `snapshot-api` | TCP 8080 / ClusterIP 80 | `/healthz`; PostgreSQL readiness at `/readyz` | None | No service-account token |
| `log-api` | TCP 8181 / ClusterIP 80 | Local `/healthz`; `/readyz` checks Kubernetes API access and currently fails with DNS-only egress | None | Namespaced `demo` Role reads pod metadata and `pods/log` |
| `collector` | CronJob, every four hours | Finite Job; no service probes | 8 MiB memory-backed `/tmp` | No service-account token |

The application pods declare UID/GID 10001, non-root execution, RuntimeDefault seccomp, read-only root filesystems, dropped capabilities, and disabled privilege escalation. The log API alone receives a service-account token for its read-only Kubernetes calls.

The collector samples data available to its own pod: pod name, kernel release, architecture, Python version, logical CPU count, cgroup CPU quota, cgroup memory usage and limit, and one-minute load average. Kernel release and load average describe the node. The collector does not inspect the host filesystem or other workloads. Each successful Job stores one idempotent row and removes samples older than 30 days. The browser shows the most recent stored sample and its timestamp.

## Browser and component-log access

For a local cluster, forward the internal web Service and open it in a normal browser on the host:

```sh
kubectl -n demo port-forward service/web-app 8080:80
# Open http://127.0.0.1:8080
```

This uses the caller's Kubernetes credentials and does not expose a public Service. The Nginx route for raw logs returns HTTP 503 until an identity-verifying authentication integration is implemented and verified. Header presence alone is not authentication.

For local development, `kubectl proxy --address=127.0.0.1 --port=8001` can provide the log API process its API path. Start `KUBERNETES_API_URL=http://127.0.0.1:8001 bun run dev:log-api`, then `bun run dev`. The local API uses the user's `kubectl proxy` identity and requires `list pods` and `get pods/log` in `demo`.

In the Kubernetes deployment, the log API has a scoped Role but its network policy currently permits DNS only. Configure an exact API-server path for the selected control plane and verify CNI behavior before enabling that egress. The read-log route remains unavailable until a real user-identity check is implemented and verified.

## Scaling and recovery

The web app, snapshot API, and log API Deployments request three replicas and have HPAs configured from three to six replicas at a 70% average CPU target. HPA operation requires the cluster metrics API and capacity for the maximum replica counts. Three-zone spread constraints require three real zones and can leave replacement pods pending if a zone is missing. PDBs limit voluntary disruption; they do not protect against a zone failure.

The datastore manifests use CloudNativePG with three instances and persistent data and WAL volumes. Before use, install a compatible operator, provision the `SecretStore`, select storage, deliver the database CA and versioned credentials, configure backups, and test a restore. A three-instance declaration alone is not recovery evidence.

Namespace-wide default-deny NetworkPolicies and workload-specific allows depend on an enforcing CNI and target-specific pod labels. The `demo` namespace also pins Restricted Pod Security Admission to Kubernetes v1.35. Check this against the selected target version before installation.

## Artifact checks

CI builds and scans commit-tagged local images and attaches CycloneDX component reports for those images. The pipeline does not publish or sign release images or bind reports to published image digests. A release therefore still requires published immutable image references, registry verification, signature/provenance policy, final image scans, and SBOMs tied to the exact released artifacts.

Before installation, verify the cluster and CNI versions, three-zone topology, metrics API, CloudNativePG operator and CRD, storage class, External Secrets provider, database TLS and backup/restore, API-server egress for the log API, and the web-app image references. No cluster was started or changed for this handoff.
