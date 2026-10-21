# Local Kubernetes System Snapshot

A hardened cloud-native demonstration stack featuring an unprivileged Vite dashboard, a unified FastAPI telemetry service, and a scheduled collector CronJob backed by PostgreSQL under strict Zero-Trust Kubernetes contracts.

For the screenshot-backed cluster startup walkthrough, see the [Kubernetes Setup Guide](<docs/Kubernetes Setup Guide.pdf>) and its [editable Markdown source](<docs/Kubernetes Setup Guide.md>).

---

## Quick Start (One Script Starts All)

To start the local FastAPI and Vite processes:

```bash
./start.sh
```

Or using Bun / Make:
```bash
bun start          # or: make start
```

This initializes the virtual environment with hash-verified dependencies when needed, builds the frontend when needed, starts both services over HTTPS, and opens **`https://takehome.localhost:5173`**. The local hostname resolves to loopback and needs no hosts-file entry. On first use, the dashboard certificate is issued by a local CA stored under `~/.config/takehome/tls`; trust that CA in your browser's OS trust store to remove `ERR_CERT_AUTHORITY_INVALID` before entering the log key. This mode does not create a database or run the collector.

Before starting, the script checks required commands and reuses installed versions that meet the project requirements. It offers to install missing tools or upgrade Bun below 1.3.14 and Helm below 4.0.0, using Homebrew on macOS or apt sources already configured on Debian/Ubuntu. It does not add package repositories; if a suitable version is unavailable from those sources, the script names it. A stopped Docker engine also requires confirmation before the script starts it.

---

## Single-Command Modes

| Command | Action |
| :--- | :--- |
| **`./start.sh`** | **Local developer stack:** Runs the FastAPI API on `https://localhost:8444` and Vite dashboard on `https://takehome.localhost:5173` with live reload. |
| **`./start.sh k8s`** | **Kubernetes cluster mode:** Builds local images, prepares protected local credentials, deploys CloudNativePG and Kustomize resources, applies the database schema, runs an initial collector Job, and port-forwards HTTPS to `https://takehome.localhost:8443`. |
| **`./start.sh check`** | **Security & compliance suite:** Runs Bandit SAST, cross-platform `pip-audit`, Trivy vulnerability scans, and outputs a CycloneDX SBOM. |

---

## Architecture at a Glance

* **Frontend ([`app/`](Takehome/app)):** Vite dashboard displaying live Kubernetes pod-readiness counts, persisted system measurements, and protected component-log views.
* **Unified API ([`api/main.py`](Takehome/api/main.py)):** Asynchronous FastAPI + Uvicorn service serving telemetry snapshots (`/api/v1/snapshot`), live pod readiness (`/api/v1/overview`), protected pod logs (`/api/v1/logs/{component}`), and liveness probes (`/healthz`).
* **Collector ([`collector/main.py`](Takehome/collector/main.py)):** Scheduled Kubernetes CronJob executing every 4 hours to sample container metrics and record them idempotently into PostgreSQL.
* **Declarative Infrastructure ([`k8s/`](Takehome/k8s)):**
  - **Zero Trust:** Restricted Pod Security Standards (`runAsUser: 10001`, `readOnlyRootFilesystem: true`, `capabilities.drop: ["ALL"]`, `seccompProfile.type: RuntimeDefault`).
  - **Network Isolation:** Namespace default-deny baseline with explicit DNS, datastore, and web-to-API ingress/egress allowlists.
  - **Resilience:** 3-replica baseline with zone spreading, PodDisruptionBudgets, and Horizontal Pod Autoscalers.

### Protected raw logs

The API reads logs only for fixed component selectors in the `demo` namespace. Its service account has a namespace-scoped Role limited to listing pod metadata and reading `pods/log`. The browser sends a 256-bit shared bearer secret; the API validates it in constant time against the read-only mounted file from immutable Secret `api-log-access-v1`. The browser-to-dashboard connection uses HTTPS, and the dashboard verifies HTTPS on its connection to the API, so the bearer secret is encrypted on both network hops. Log responses use `no-store`, and the frontend keeps the credential in page memory only. It is cleared by **Lock logs** or when the page is closed. The shared credential confirms possession; it does not identify individual users or provide per-user audit attribution.

In local Kubernetes mode, `./start.sh k8s` generates `k8s/overlays/local/log-access-token` with mode `0600` if needed and creates the immutable Secret without printing its value. The dashboard's log prompt can read that file through the browser file picker, or accept the token pasted from a trusted local secret manager. The file is ignored by Git. If the Secret exists but the workstation token file is missing or does not match, startup stops; restore the file or rotate to a new versioned Secret. Local HTTPS certificate keys and the CA key live outside the repository in an owner-only directory at `~/.config/takehome/tls` (or `TAKEHOME_TLS_DIR`). The dashboard uses `https://takehome.localhost`; its certificate is issued for that hostname and the older localhost name. The root CA certificate (`ca-v2.crt`) must still be trusted by the browser to clear `ERR_CERT_AUTHORITY_INVALID`; the hostname alone cannot establish trust, and startup does not change OS trust settings. Production uses ExternalSecret contracts in `k8s/base/integrations/log-access.yaml` and `k8s/base/integrations/tls.yaml`; configure their provider keys and certificates for the deployment hostnames before applying the production base. Rotate by provisioning a new Secret version and updating the workload reference.

The **Live stack** view reads readiness from Kubernetes every 30 seconds and shows ready/observed pod counts for PostgreSQL, Snapshot API, and Web dashboard. Collector cadence is displayed as configured schedule; the displayed last-success time comes from the latest persisted sample, not a live job-health check.

### Local cluster limits

The local overlay runs on one laptop failure domain with a single PostgreSQL instance and node-local storage. It demonstrates the workload configuration and does not provide database failover, off-node backups, or zone-level availability.
