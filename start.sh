#!/usr/bin/env bash
# ==============================================================================
# One-Script Starter for Local Kubernetes System Snapshot
#
# Usage:
#   ./start.sh          -> Starts local development stack (API + Frontend UI)
#   ./start.sh k8s      -> Deploys and port-forwards the Kubernetes cluster stack
#   ./start.sh check    -> Runs SAST, dependency audits, and SBOM compliance suite
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

MODE="${1:-local}"

# ANSI Colors
BOLD="\033[1m"
GREEN="\033[32m"
BLUE="\033[34m"
YELLOW="\033[33m"
CYAN="\033[36m"
RESET="\033[0m"

log_info()  { printf "${BLUE}${BOLD}==>${RESET} %s\n" "$*"; }
log_ok()    { printf "${GREEN}${BOLD}✓${RESET} %s\n" "$*"; }
log_warn()  { printf "${YELLOW}${BOLD}!${RESET} %s\n" "$*"; }

print_banner() {
  printf "${CYAN}${BOLD}"
  cat << "EOF"
  _                   _   _   _                             _           
 | |   ___  __ __ _ _| | | |_| |_ __ _ __ _ _  _ _ _  _  _ (_)__  _ _ __ 
 | |__/ _ \/ _/ _` | | | | / / ' \/ _` / _| '_| || | '_| || | / _| || / _ \
 |____\___/\__\__,_|_|_| |_\_\_||_\__,_\__|_|  \_,_|_|  \_,_|_\__|\_,_\___/
EOF
  printf "${RESET}\n"
}

# ------------------------------------------------------------------------------
# Mode: Security & Compliance Check
# ------------------------------------------------------------------------------
run_check() {
  print_banner
  log_info "Running comprehensive compliance and security test suite..."
  make check
  log_ok "All security scans, dependency audits, and SBOM generations passed!"
  exit 0
}

# ------------------------------------------------------------------------------
# Mode: Kubernetes Minikube Deployment
# ------------------------------------------------------------------------------
run_k8s() {
  print_banner
  log_info "Starting Kubernetes cluster deployment mode..."

  for cmd in kubectl minikube helm bun; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      log_warn "Missing required CLI tool: $cmd"
      exit 1
    fi
  done

  log_info "Checking Minikube profile 'takehome'..."
  if ! minikube status -p takehome >/dev/null 2>&1; then
    log_info "Starting Minikube (3 nodes, Calico CNI)..."
    minikube start -p takehome --nodes 3 --cni calico
  fi
  kubectl config use-context takehome

  log_info "Bootstrapping namespace and admission controls..."
  kubectl apply -k k8s/bootstrap

  # Local credentials are immutable, versioned Secrets. Create the complete
  # set once from the ignored workstation-only file; never patch their data.
  local_secret_file="k8s/overlays/local/secrets-local.yaml"
  local_secret_names=(datastore-credentials-v1 api-datastore-credentials-v1 collector-datastore-credentials-v1)
  local_secret_missing=0
  for secret_name in "${local_secret_names[@]}"; do
    if kubectl -n demo get secret "$secret_name" >/dev/null 2>&1; then
      continue
    else
      local_secret_missing=$((local_secret_missing + 1))
    fi
  done
  if [ "$local_secret_missing" -eq "${#local_secret_names[@]}" ]; then
    if [ ! -f "$local_secret_file" ]; then
      log_warn "Missing local credential file: $local_secret_file"
      exit 1
    fi
    log_info "Creating the versioned local database credentials..."
    kubectl -n demo create -f "$local_secret_file"
  elif [ "$local_secret_missing" -gt 0 ]; then
    log_warn "Only part of the immutable local credential set exists. Restore or rotate the complete versioned set before deployment."
    exit 1
  else
    log_ok "Versioned local database credentials are already installed."
  fi

  log_info "Deploying CloudNativePG operator..."
  if ! helm status cnpg -n cnpg-system >/dev/null 2>&1; then
    helm repo add cnpg https://cloudnative-pg.github.io/charts --force-update >/dev/null 2>&1 || true
    helm upgrade --install cnpg cnpg/cloudnative-pg \
      --namespace cnpg-system \
      --create-namespace \
      --values deploy/cnpg-values-local.yaml
  fi

  log_info "Applying declarative manifests (Kustomize local overlay)..."
  kubectl apply -k k8s/overlays/local

  log_info "Waiting for PostgreSQL and applying the initial snapshot schema..."
  kubectl -n demo wait --for=condition=Ready cluster/postgres-cluster --timeout=180s
  DB_POD="$(kubectl -n demo get pods -l cnpg.io/cluster=postgres-cluster,role=primary -o jsonpath='{.items[0].metadata.name}')"
  if [ -z "$DB_POD" ]; then
    log_warn "CloudNativePG did not report a primary pod."
    exit 1
  fi
  SCHEMA_PRESENT="$(kubectl -n demo exec "$DB_POD" -- psql -U postgres -d demo -tAc "SELECT to_regclass('public.system_snapshots') IS NOT NULL" | tr -d '[:space:]')"
  if [ "$SCHEMA_PRESENT" != "t" ]; then
    kubectl -n demo exec -i "$DB_POD" -- psql -v ON_ERROR_STOP=1 -U postgres -d demo < datastore/schema.sql
  else
    kubectl -n demo exec "$DB_POD" -- psql -v ON_ERROR_STOP=1 -U postgres -d demo \
      -c "GRANT SELECT ON public.system_snapshots TO snapshot_api; GRANT INSERT, DELETE ON public.system_snapshots TO snapshot_collector; GRANT SELECT (run_id, sampled_at) ON public.system_snapshots TO snapshot_collector;"
  fi

  log_info "Waiting for workloads to become ready..."
  kubectl -n demo rollout status deployment/web-app --timeout=120s
  kubectl -n demo rollout status deployment/snapshot-api --timeout=120s

  COLLECTOR_JOB="collector-manual-$(date -u +%s)-$$"
  log_info "Running an initial system snapshot..."
  kubectl -n demo create job --from=cronjob/collector "$COLLECTOR_JOB"
  if ! kubectl -n demo wait --for=condition=complete "job/$COLLECTOR_JOB" --timeout=300s; then
    kubectl -n demo get job "$COLLECTOR_JOB" -o wide || true
    kubectl -n demo logs "job/$COLLECTOR_JOB" --tail=40 || true
    exit 1
  fi

  log_ok "Cluster stack is live! Starting local port-forward to web-app..."
  printf "\n${GREEN}${BOLD}Web Dashboard:${RESET} http://127.0.0.1:8080\n"
  if lsof -ti tcp:8080 >/dev/null 2>&1; then
    if curl -fsS http://127.0.0.1:8080/api/v1/snapshot 2>/dev/null | grep -q '"snapshot"'; then
      log_ok "The Kubernetes dashboard is already reachable at http://127.0.0.1:8080."
      exit 0
    fi
    log_warn "Port 8080 is occupied by another service; stop it before starting the dashboard port-forward."
    exit 1
  fi
  printf "Press ${BOLD}Ctrl+C${RESET} to terminate the port-forward.\n\n"
  exec kubectl -n demo port-forward service/web-app 8080:80
}

# ------------------------------------------------------------------------------
# Mode: Local Stack (Zero-Cluster Fast Start)
# ------------------------------------------------------------------------------
run_local() {
  print_banner
  log_info "Starting local developer stack (FastAPI Backend + Vite Frontend)..."

  # 1. Check prerequisites
  if ! command -v bun >/dev/null 2>&1; then
    log_warn "Bun is required to run the frontend. Install from https://bun.sh"
    exit 1
  fi

  # 2. Virtual environment auto-initialization
  if [ ! -d ".venv" ]; then
    log_info "Initializing Python virtual environment (.venv)..."
    if command -v uv >/dev/null 2>&1; then
      uv venv .venv
      uv pip install -r datastore/requirements.lock
    else
      python3 -m venv .venv
      .venv/bin/pip install --require-hashes -r datastore/requirements.lock
    fi
    log_ok "Virtual environment configured with hash-verified dependencies."
  fi

  # 3. Build frontend assets if missing
  if [ ! -d "app/dist" ]; then
    log_info "Compiling frontend production bundle..."
    bun run build
    log_ok "Frontend bundle built."
  fi

  # 4. Fail clearly when a requested local port is already in use.
  for port in 8080 5173; do
    PID=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$PID" ]; then
      log_warn "Port $port is already in use by PID(s): $PID"
      exit 1
    fi
  done

  # Trap cleanup on exit
  API_PID=""
  WEB_PID=""
  cleanup() {
    printf "\n"
    log_info "Shutting down services cleanly..."
    [ -n "$API_PID" ] && kill -TERM "$API_PID" 2>/dev/null || true
    [ -n "$WEB_PID" ] && kill -TERM "$WEB_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    wait "$WEB_PID" 2>/dev/null || true
    log_ok "All services stopped."
    exit 0
  }
  trap cleanup EXIT INT TERM

  # 5. Start unified FastAPI backend
  log_info "Launching Unified FastAPI backend on http://127.0.0.1:8080..."
  PORT=8080 API_HOST=127.0.0.1 .venv/bin/python api/main.py &
  API_PID=$!

  # Wait briefly for API readiness
  API_READY=0
  for _ in {1..30}; do
    if curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
      API_READY=1
      break
    fi
    sleep 0.2
  done
  if [ "$API_READY" -ne 1 ]; then
    log_warn "Snapshot API did not become healthy."
    exit 1
  fi
  log_ok "API server is healthy."

  # 6. Start Vite frontend dev server
  log_info "Launching Vite web dashboard..."
  bun run --filter @local-k8s-demo/web dev &
  WEB_PID=$!

  # Wait for Vite dev server
  WEB_READY=0
  for _ in {1..30}; do
    if curl -fsS http://127.0.0.1:5173 >/dev/null 2>&1; then
      WEB_READY=1
      break
    fi
    sleep 0.2
  done
  if [ "$WEB_READY" -ne 1 ]; then
    log_warn "Vite web dashboard did not become healthy."
    exit 1
  fi
  log_ok "Web dashboard is live."

  # 7. Print access endpoints
  printf "\n"
  printf "┌──────────────────────────────────────────────────────────────┐\n"
  printf "│ ${GREEN}${BOLD}Local Stack Ready!${RESET}                                            │\n"
  printf "├──────────────────────────────────────────────────────────────┤\n"
  printf "│  ${BOLD}Web Dashboard:${RESET}    ${CYAN}http://localhost:5173${RESET}                           │\n"
  printf "│  ${BOLD}Snapshot API:${RESET}     ${CYAN}http://localhost:8080/api/v1/snapshot${RESET}           │\n"
  printf "│  ${BOLD}API Health:${RESET}       ${CYAN}http://localhost:8080/healthz${RESET}                   │\n"
  printf "├──────────────────────────────────────────────────────────────┤\n"
  printf "│  Press ${BOLD}Ctrl+C${RESET} in this terminal to shut down all services.     │\n"
  printf "└──────────────────────────────────────────────────────────────┘\n\n"

  # Open browser automatically on macOS if supported
  if command -v open >/dev/null 2>&1; then
    open "http://localhost:5173" 2>/dev/null || true
  fi

  wait
}

case "$MODE" in
  local|"")
    run_local
    ;;
  k8s|cluster)
    run_k8s
    ;;
  check|test)
    run_check
    ;;
  *)
    printf "Usage: %s [local|k8s|check]\n" "$0"
    exit 1
    ;;
esac
