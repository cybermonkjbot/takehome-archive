# Local Kubernetes System Snapshot

A hardened cloud-native demonstration stack featuring an unprivileged Vite dashboard, a unified FastAPI telemetry service, and a scheduled collector CronJob backed by PostgreSQL under strict Zero-Trust Kubernetes contracts.

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

This initializes the virtual environment with hash-verified dependencies when needed, builds the frontend when needed, starts both services, and opens **`http://localhost:5173`**. Persisted snapshots require a configured PostgreSQL endpoint, credential files, and an applied schema; this mode does not create a database or run the collector.

---

## Single-Command Modes

| Command | Action |
| :--- | :--- |
| **`./start.sh`** | **Local developer stack:** Runs unified FastAPI backend (`:8080`) and Vite dashboard (`:5173`) with live reload. |
| **`./start.sh k8s`** | **Kubernetes cluster mode:** Verifies Minikube, deploys CloudNativePG and Kustomize resources, applies the initial database schema, runs a collector Job, and port-forwards to `http://127.0.0.1:8080`. Local images and the workstation-only immutable credential file must already be provisioned. |
| **`./start.sh check`** | **Security & compliance suite:** Runs Bandit SAST, cross-platform `pip-audit`, Trivy vulnerability scans, and outputs a CycloneDX SBOM. |

---

## Architecture at a Glance

* **Frontend ([`app/`](Takehome/app)):** Minimalist Vite dashboard displaying real-time system metrics, cgroup quotas, and component log diagnostic modals.
* **Unified API ([`api/main.py`](Takehome/api/main.py)):** Asynchronous FastAPI + Uvicorn service serving telemetry snapshots (`/api/v1/snapshot`), scoped container pod logs (`/api/v1/logs/{component}`), and liveness probes (`/healthz`).
* **Collector ([`collector/main.py`](Takehome/collector/main.py)):** Scheduled Kubernetes CronJob executing every 4 hours to sample container metrics and record them idempotently into PostgreSQL.
* **Declarative Infrastructure ([`k8s/`](Takehome/k8s)):**
  - **Zero Trust:** Restricted Pod Security Standards (`runAsUser: 10001`, `readOnlyRootFilesystem: true`, `capabilities.drop: ["ALL"]`, `seccompProfile.type: RuntimeDefault`).
  - **Network Isolation:** Namespace default-deny baseline with explicit DNS, datastore, and web-to-API ingress/egress allowlists.
  - **Resilience:** 3-replica baseline with zone spreading, PodDisruptionBudgets, and Horizontal Pod Autoscalers.

The pod-log endpoint is implemented in the unified API and uses a namespace-scoped Role to list pods and read their logs. The browser log route returns `503` until a selected identity-verifying Gateway is configured; raw logs stay unavailable through the dashboard while that security integration is unresolved.

The local overlay runs on one laptop failure domain with a single PostgreSQL instance and node-local storage. It demonstrates the workload configuration and does not provide database failover, off-node backups, or zone-level availability.
