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
if [ "$#" -gt 1 ]; then
  printf "Usage: %s [local|k8s|check|help]\n" "$0" >&2
  exit 2
fi

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

confirm_action() {
  local prompt="$1"
  local answer
  if [ ! -r /dev/tty ]; then
    log_warn "This action needs an interactive terminal. Run the command again in Terminal to review and approve it."
    return 1
  fi
  printf "%s [y/N] " "$prompt" > /dev/tty
  IFS= read -r answer < /dev/tty || return 1
  case "$answer" in
    y|Y|yes|YES|Yes) return 0 ;;
    *) return 1 ;;
  esac
}

brew_formula_for() {
  case "$1" in
    kubectl|minikube|helm|bun|lsof) printf '%s' "$1" ;;
    openssl) printf '%s' 'openssl@3' ;;
    python3) printf '%s' 'python' ;;
    docker) printf '%s' 'docker' ;;
    *) return 1 ;;
  esac
}

apt_package_for() {
  case "$1" in
    docker) printf '%s' 'docker.io' ;;
    kubectl|minikube|helm|bun|openssl|python3|lsof) printf '%s' "$1" ;;
    *) return 1 ;;
  esac
}

minimum_version_for() {
  case "$1" in
    bun) printf '%s' '1.3.14' ;;
    helm) printf '%s' '4.0.0' ;;
    *) return 0 ;;
  esac
}

installed_version_for() {
  case "$1" in
    bun) bun --version 2>/dev/null ;;
    helm) helm version --template '{{.Version}}' 2>/dev/null ;;
    *) return 1 ;;
  esac
}

version_at_least() {
  awk -v actual="$1" -v required="$2" 'BEGIN {
    sub(/^[^0-9]*/, "", actual)
    sub(/^[^0-9]*/, "", required)
    sub(/^[0-9]+:/, "", actual)
    sub(/^[0-9]+:/, "", required)
    sub(/[+-].*$/, "", actual)
    sub(/[+-].*$/, "", required)
    sub(/~.*$/, "", actual)
    sub(/~.*$/, "", required)
    split(actual, a, /[.]/)
    split(required, r, /[.]/)
    for (i = 1; i <= 3; i++) {
      av = a[i] + 0
      rv = r[i] + 0
      if (av > rv) exit 0
      if (av < rv) exit 1
    }
    exit 0
  }'
}

command_is_ready() {
  local command_name="$1"
  local minimum actual
  if ! command -v "$command_name" >/dev/null 2>&1; then
    return 1
  fi
  minimum="$(minimum_version_for "$command_name")"
  [ -z "$minimum" ] && return 0
  actual="$(installed_version_for "$command_name" || true)"
  [ -n "$actual" ] && version_at_least "$actual" "$minimum"
}

missing_commands() {
  local command_name
  for command_name in "$@"; do
    command_is_ready "$command_name" || printf '%s\n' "$command_name"
  done
}

bootstrap_homebrew() {
  local installer_file brew_path
  for brew_path in /opt/homebrew/bin/brew /usr/local/bin/brew "$HOME/.linuxbrew/bin/brew" /home/linuxbrew/.linuxbrew/bin/brew; do
    if [ -x "$brew_path" ]; then
      export PATH="$(dirname "$brew_path"):$PATH"
      break
    fi
  done
  if command -v brew >/dev/null 2>&1; then
    return 0
  fi
  if [ "$(uname -s)" != "Darwin" ]; then
    return 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    log_warn "Homebrew is missing and curl is unavailable, so I cannot install the package manager automatically."
    return 1
  fi
  log_info "Homebrew is needed to install missing command-line tools on macOS. Its official installer may request administrator approval and install Apple's Command Line Tools."
  log_info "Installer source: https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
  if ! confirm_action "Download and run the official Homebrew installer now?"; then
    log_warn "Homebrew installation was declined. Install it later, then rerun ./start.sh $MODE."
    return 1
  fi

  installer_file="$(mktemp "${TMPDIR:-/tmp}/takehome-homebrew.XXXXXX")"
  chmod 700 "$installer_file"
  if ! curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    --output "$installer_file" \
    https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh; then
    rm -f "$installer_file"
    log_warn "The official Homebrew installer could not be downloaded. Check your connection and retry."
    return 1
  fi
  if ! /bin/bash "$installer_file"; then
    rm -f "$installer_file"
    log_warn "Homebrew did not finish installing. Resolve the installer message, then rerun ./start.sh $MODE."
    return 1
  fi
  rm -f "$installer_file"

  for brew_path in /opt/homebrew/bin/brew /usr/local/bin/brew "$HOME/.linuxbrew/bin/brew" /home/linuxbrew/.linuxbrew/bin/brew; do
    if [ -x "$brew_path" ]; then
      export PATH="$(dirname "$brew_path"):$PATH"
      break
    fi
  done
  if ! command -v brew >/dev/null 2>&1; then
    log_warn "Homebrew finished installing, but its command is not on PATH yet. Follow its printed shell setup step, open a new terminal, and rerun ./start.sh $MODE."
    return 1
  fi
  log_ok "Homebrew is installed."
}

install_missing_commands() {
  local required_commands=("$@")
  local missing=()
  local formulas=()
  local formulas_to_upgrade=()
  local casks=()
  local apt_packages=()
  local unresolved=()
  local command_name package_name minimum actual candidate

  for command_name in "${required_commands[@]}"; do
    minimum="$(minimum_version_for "$command_name")"
    if [ -n "$minimum" ] && command -v "$command_name" >/dev/null 2>&1; then
      actual="$(installed_version_for "$command_name" || true)"
      if [ -z "$actual" ] || ! version_at_least "$actual" "$minimum"; then
        log_warn "$command_name ${actual:-unknown version} is below the required version $minimum; I will offer an upgrade."
      fi
    fi
  done

  missing=()
  while IFS= read -r command_name; do
    [ -n "$command_name" ] && missing+=("$command_name")
  done < <(missing_commands "${required_commands[@]}")

  if [ "${#missing[@]}" -eq 0 ]; then
    log_ok "All required commands are already available."
    return 0
  fi

  log_warn "Missing commands: ${missing[*]}"

  if [ "$(uname -s)" = "Darwin" ]; then
    if ! bootstrap_homebrew; then
      log_warn "Install Homebrew once, then rerun ./start.sh $MODE. No project dependency has been installed."
      return 1
    fi
    for command_name in "${missing[@]}"; do
      if [ "$command_name" = "docker" ]; then
        casks+=(docker)
      elif package_name="$(brew_formula_for "$command_name")"; then
        if brew list --formula --versions "$package_name" >/dev/null 2>&1; then
          formulas_to_upgrade+=("$package_name")
        else
          formulas+=("$package_name")
        fi
      else
        unresolved+=("$command_name")
      fi
    done
    if [ "${#formulas[@]}" -eq 0 ] && [ "${#formulas_to_upgrade[@]}" -eq 0 ] && [ "${#casks[@]}" -eq 0 ]; then
      log_warn "Homebrew cannot provide these commands: ${unresolved[*]}"
      return 1
    fi
    [ "${#formulas[@]}" -gt 0 ] && log_info "Homebrew install plan: ${formulas[*]}"
    [ "${#formulas_to_upgrade[@]}" -gt 0 ] && log_info "Homebrew upgrade plan: ${formulas_to_upgrade[*]}"
    [ "${#casks[@]}" -gt 0 ] && log_info "Homebrew cask plan: ${casks[*]}"
    if ! confirm_action "Install or upgrade the listed commands with Homebrew now?"; then
      log_warn "No tools were installed. Rerun ./start.sh $MODE and approve the install prompt when ready."
      return 1
    fi
    if [ "${#formulas_to_upgrade[@]}" -gt 0 ] && ! brew upgrade --formula "${formulas_to_upgrade[@]}"; then
      log_warn "Homebrew could not upgrade the command-line tools. Review its message and rerun ./start.sh $MODE."
      return 1
    fi
    if [ "${#formulas[@]}" -gt 0 ] && ! brew install --formula "${formulas[@]}"; then
      log_warn "Homebrew could not install the command-line tools. Review its message and rerun ./start.sh $MODE."
      return 1
    fi
    if [ "${#casks[@]}" -gt 0 ] && ! brew install --cask "${casks[@]}"; then
      log_warn "Homebrew could not install Docker Desktop. Review its message and rerun ./start.sh $MODE."
      return 1
    fi
    export PATH="$(brew --prefix)/bin:$(brew --prefix)/sbin:$PATH"
    if brew --prefix openssl@3 >/dev/null 2>&1; then
      export PATH="$(brew --prefix openssl@3)/bin:$PATH"
    fi
  elif command -v apt-get >/dev/null 2>&1 && command -v apt-cache >/dev/null 2>&1; then
    if [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null 2>&1; then
      log_warn "sudo is required to use the configured apt sources. No tools were installed."
      return 1
    fi
    log_info "apt refresh plan: update package metadata from the sources already configured on this system. No repositories will be added."
    if ! confirm_action "Refresh apt's package list so I can check which missing tools are available?"; then
      log_warn "No tools were installed. Rerun ./start.sh $MODE and approve the package-list refresh when ready."
      return 1
    fi
    if [ "$(id -u)" -eq 0 ]; then
      if ! apt-get update; then
        log_warn "apt could not refresh its configured package sources. Check the apt message and retry."
        return 1
      fi
    elif ! sudo apt-get update; then
      log_warn "apt could not refresh its configured package sources. Check the apt message and retry."
      return 1
    fi

    for command_name in "${missing[@]}"; do
      if ! package_name="$(apt_package_for "$command_name")"; then
        unresolved+=("$command_name")
      else
        candidate="$(apt-cache policy "$package_name" 2>/dev/null | awk '$1 == "Candidate:" { print $2; exit }')"
        minimum="$(minimum_version_for "$command_name")"
        if [ -z "$candidate" ] || [ "$candidate" = "(none)" ]; then
          unresolved+=("$command_name")
        elif [ -n "$minimum" ] && ! version_at_least "$candidate" "$minimum"; then
          unresolved+=("$command_name (apt candidate $candidate is below $minimum)")
        else
          apt_packages+=("$package_name")
        fi
      fi
    done
    if [ "${#apt_packages[@]}" -eq 0 ]; then
      log_warn "None of the missing commands are available at the required versions in the configured apt package sources. No new repositories will be added automatically."
      log_warn "Install these from their official instructions, then rerun: ${unresolved[*]}"
      return 1
    fi
    log_info "apt install/upgrade plan: ${apt_packages[*]}"
    if [ "${#unresolved[@]}" -gt 0 ]; then
      log_warn "These commands are not available in the configured apt sources and will still need attention: ${unresolved[*]}"
    fi
    if ! confirm_action "Install or upgrade these available tools now? This may request your administrator password and may start Docker's service."; then
      log_warn "No tools were installed. Rerun ./start.sh $MODE and approve the install prompt when ready."
      return 1
    fi
    if [ "$(id -u)" -eq 0 ]; then
      if ! apt-get install -y --no-install-recommends "${apt_packages[@]}"; then
        log_warn "apt could not install the available tools. Check the package-manager message and rerun ./start.sh $MODE."
        return 1
      fi
    elif command -v sudo >/dev/null 2>&1; then
      if ! sudo apt-get install -y --no-install-recommends "${apt_packages[@]}"; then
        log_warn "apt could not install the available tools. Check the package-manager message and rerun ./start.sh $MODE."
        return 1
      fi
    else
      log_warn "sudo is required to install the missing tools. No tools were installed."
      return 1
    fi
  else
    log_warn "Automatic prerequisite installation supports Homebrew on macOS and packages already available in Debian or Ubuntu apt sources."
    log_warn "No third-party package sources will be added. Install these commands, then rerun ./start.sh $MODE: ${missing[*]}"
    return 1
  fi

  missing=()
  while IFS= read -r command_name; do
    [ -n "$command_name" ] && missing+=("$command_name")
  done < <(missing_commands "${required_commands[@]}")
  if [ "${#missing[@]}" -gt 0 ]; then
    log_warn "Still missing or below the required version after installation: ${missing[*]}"
    log_warn "Follow the package manager message above, then rerun ./start.sh $MODE."
    return 1
  fi
  log_ok "Required commands are ready."
}

ensure_docker_engine() {
  local attempt
  if docker info --format '{{.OSType}}' >/dev/null 2>&1; then
    return 0
  fi

  if [ "$(uname -s)" = "Darwin" ] && command -v open >/dev/null 2>&1; then
    if ! confirm_action "Docker Desktop is installed but stopped. Open it and wait for its engine now?"; then
      log_warn "Start Docker Desktop, wait until its engine is running, then rerun ./start.sh k8s."
      return 1
    fi
    open -a Docker >/dev/null 2>&1 || {
      log_warn "Docker Desktop could not be opened. Open it manually, wait for its engine, then rerun ./start.sh k8s."
      return 1
    }
  elif [ "$(uname -s)" = "Linux" ] && command -v systemctl >/dev/null 2>&1 \
    && systemctl list-unit-files --type=service --no-legend docker.service 2>/dev/null \
      | awk '$1 == "docker.service" { found = 1 } END { exit !found }'; then
    if ! confirm_action "The Docker service is stopped. Start the system service now? This may request your administrator password."; then
      log_warn "Start the Docker service, then rerun ./start.sh k8s."
      return 1
    fi
    if [ "$(id -u)" -eq 0 ]; then
      systemctl start docker || return 1
    elif command -v sudo >/dev/null 2>&1; then
      sudo systemctl start docker || return 1
    else
      log_warn "sudo is required to start Docker. Start it manually, then rerun ./start.sh k8s."
      return 1
    fi
  else
    log_warn "Docker is installed but its engine is not responding. Start Docker Desktop or the Docker service, then rerun ./start.sh k8s."
    return 1
  fi

  log_info "Waiting for Docker's engine to become ready..."
  for attempt in {1..60}; do
    if docker info --format '{{.OSType}}' >/dev/null 2>&1; then
      log_ok "Docker's engine is ready."
      return 0
    fi
    sleep 2
  done
  log_warn "Docker's engine did not become ready. Check Docker Desktop or the Docker service, then rerun ./start.sh k8s."
  return 1
}

get_tls_dir() {
  python3 -c 'import os; from pathlib import Path; print(Path(os.environ.get("TAKEHOME_TLS_DIR", Path.home() / ".config" / "takehome" / "tls")).expanduser())'
}
https_check() {
  python3 - "$1" "$2" "${3:-}" <<'PY'
import json
import ssl
import sys
from urllib.request import urlopen

ca_file, url, json_field = sys.argv[1:]
try:
    context = ssl.create_default_context(cafile=ca_file)
    with urlopen(url, context=context, timeout=5) as response:
        if response.status != 200:
            raise SystemExit(1)
        if json_field and not json.load(response).get(json_field):
            raise SystemExit(1)
except Exception:
    raise SystemExit(1)
PY
}

print_usage() {
  cat <<EOF
Usage: $0 [local|k8s|check|help]

  local   Start the local API and Vite dashboard (default)
  k8s     Build and start the complete local Minikube stack
  check   Run the repository security and compliance checks
  help    Show this help

Missing setup commands are offered for installation through Homebrew or the
configured Debian/Ubuntu apt sources. The script skips commands already found
and asks before installing. It does not add package repositories automatically.
EOF
}

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
  log_info "Preparing the local Kubernetes stack..."

  if ! install_missing_commands docker kubectl minikube helm bun openssl python3 lsof; then
    exit 1
  fi
  local_tls_dir="$(get_tls_dir)"

  if ! ensure_docker_engine; then
    exit 1
  fi
  docker_metadata="$(docker info --format '{{.OSType}} {{.Architecture}}' 2>/dev/null)"
  docker_os="${docker_metadata%% *}"
  if [ "$docker_os" != "linux" ]; then
    log_warn "Docker is using the '$docker_os' engine. Switch Docker Desktop to its Linux container engine and try again."
    exit 1
  fi

  log_info "Checking Minikube profile 'takehome'..."
  if ! minikube status -p takehome >/dev/null 2>&1; then
    log_info "Starting Minikube with Docker, three nodes, and Calico networking..."
    if ! minikube start -p takehome --driver=docker --nodes=3 --cni=calico; then
      log_warn "Minikube could not start. Check Docker Desktop has enough memory and disk, then run 'minikube status -p takehome' for details."
      exit 1
    fi
  else
    log_ok "Minikube profile 'takehome' is running."
  fi

  if ! kubectl config use-context takehome >/dev/null 2>&1; then
    log_warn "Kubernetes context 'takehome' is missing. Run 'minikube update-context -p takehome', then try again."
    exit 1
  fi

  node_arch="$(minikube -p takehome ssh -- uname -m 2>/dev/null | tr -d '\r\n' || true)"
  case "$node_arch" in
    aarch64|arm64) target_platform="linux/arm64" ;;
    x86_64|amd64) target_platform="linux/amd64" ;;
    *)
      log_warn "Could not determine the Minikube node architecture. Run 'minikube -p takehome ssh -- uname -m' and resolve the cluster issue before retrying."
      exit 1
      ;;
  esac

  log_info "Installing the exact locked Bun workspace dependencies..."
  if ! bun install --frozen-lockfile; then
    log_warn "Bun could not install the locked dependencies. Check your internet connection and bun.lock, then rerun ./start.sh k8s."
    exit 1
  fi
  log_info "Building the web dashboard..."
  if ! bun run build; then
    log_warn "The web dashboard build failed. Fix the displayed build error, then rerun ./start.sh k8s."
    exit 1
  fi

  build_and_load_image() {
    image_name="$1"
    dockerfile="$2"
    log_info "Building $image_name for $target_platform..."
    if ! docker build --platform "$target_platform" --tag "$image_name" --file "$dockerfile" .; then
      log_warn "Could not build $image_name. Check the Docker error above, then rerun ./start.sh k8s."
      exit 1
    fi
    log_info "Loading $image_name into Minikube..."
    if ! minikube -p takehome image load "$image_name" --overwrite=true; then
      log_warn "Could not load $image_name into Minikube. Check the Docker driver and available disk, then rerun ./start.sh k8s."
      exit 1
    fi
  }

  build_and_load_image "web-app:0.1.2" "app/Dockerfile"
  build_and_load_image "api:0.1.2" "api/Dockerfile"
  build_and_load_image "collector:0.1.0" "collector/Dockerfile"
  build_and_load_image "postgres-local:16.15-minimal-trixie-uid10001" "deploy/postgres-local/Dockerfile"
  log_ok "All four local workload images are built and loaded."

  log_info "Bootstrapping namespace and admission controls..."
  if ! kubectl apply -k k8s/bootstrap; then
    log_warn "The namespace could not be prepared. Check 'kubectl --context takehome get nodes', then rerun ./start.sh k8s."
    exit 1
  fi

  log_info "Preparing local HTTPS certificates and immutable TLS Secrets..."
  if ! python3 scripts/local-tls.py provision; then
    log_warn "Local HTTPS certificates could not be provisioned. Keep the owner-only CA files intact, resolve the reported issue, then rerun ./start.sh k8s."
    exit 1
  fi

  # Keep a workstation-only legacy file private without inspecting or printing it.
  if [ -f "k8s/overlays/local/secrets-local.yaml" ]; then
    chmod 600 "k8s/overlays/local/secrets-local.yaml"
  fi

  credentials_file="k8s/overlays/local/secrets-local.json"
  log_info "Preparing owner-only local credentials and versioned PostgreSQL CAs..."
  if ! python3 scripts/local-secrets.py ensure --credentials-file "$credentials_file"; then
    log_warn "Local database credentials could not be prepared. Keep the protected credentials file intact or restore its secure backup, then retry."
    exit 1
  fi
  if ! python3 scripts/local-secrets.py certificates --credentials-file "$credentials_file"; then
    log_warn "Local PostgreSQL CA setup failed. Keep the protected credentials file intact and retry after resolving the OpenSSL or Kubernetes error."
    exit 1
  fi
  if ! python3 scripts/local-secrets.py bootstrap --credentials-file "$credentials_file"; then
    log_warn "The database bootstrap Secret could not be prepared. Check Kubernetes access, then rerun ./start.sh k8s."
    exit 1
  fi
  if ! python3 scripts/local-secrets.py roles --credentials-file "$credentials_file"; then
    log_warn "The API and collector Secrets could not be prepared. Check Kubernetes access, then rerun ./start.sh k8s."
    exit 1
  fi

  # Raw-log access uses one high-entropy, shared bearer secret for this local
  # profile. Keep its source file workstation-only and never print its value.
  log_access_token_file="k8s/overlays/local/log-access-token"
  if kubectl -n demo get secret api-log-access-v1 >/dev/null 2>&1; then
    if [ ! -f "$log_access_token_file" ]; then
      log_warn "The immutable log-access Secret exists but its local token file is missing. Restore the file or rotate to a new Secret version."
      exit 1
    fi
    if ! grep -Eq '^[0-9a-f]{64}$' "$log_access_token_file"; then
      log_warn "The local log-access token file is invalid; it must contain one 256-bit hexadecimal token."
      exit 1
    fi
    secret_immutable="$(kubectl -n demo get secret api-log-access-v1 -o jsonpath='{.immutable}')"
    if [ "$secret_immutable" != "true" ]; then
      log_warn "The log-access Secret is not immutable. Replace it through a new versioned Secret before deployment."
      exit 1
    fi
    if ! python3 - "$log_access_token_file" <<'PY'
import base64
import hmac
import subprocess
import sys
from pathlib import Path

result = subprocess.run(
    ["kubectl", "-n", "demo", "get", "secret", "api-log-access-v1", "-o", "jsonpath={.data.token}"],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    check=False,
)
try:
    expected = base64.b64encode(Path(sys.argv[1]).read_bytes()).strip()
except OSError:
    raise SystemExit(1)
raise SystemExit(0 if result.returncode == 0 and hmac.compare_digest(result.stdout, expected) else 1)
PY
    then
      log_warn "The local log-access token file does not match the installed Secret. Restore it or rotate to a new Secret version."
      exit 1
    fi
    chmod 600 "$log_access_token_file"
    log_ok "Immutable log-access Secret is installed."
  else
    if [ ! -f "$log_access_token_file" ]; then
      token_temp_file="$(mktemp "${log_access_token_file}.XXXXXX")"
      openssl rand -hex 32 > "$token_temp_file"
      chmod 600 "$token_temp_file"
      mv "$token_temp_file" "$log_access_token_file"
    fi
    if ! grep -Eq '^[0-9a-f]{64}$' "$log_access_token_file"; then
      log_warn "The local log-access token file is invalid; it must contain one 256-bit hexadecimal token."
      exit 1
    fi
    chmod 600 "$log_access_token_file"
    log_info "Creating immutable versioned log-access Secret..."
    kubectl -n demo create secret generic api-log-access-v1 \
      --from-file="token=$log_access_token_file" \
      --dry-run=client -o json \
      | python3 -c 'import json, sys; secret = json.load(sys.stdin); secret["immutable"] = True; json.dump(secret, sys.stdout)' \
      | kubectl apply -f - >/dev/null
  fi

  log_info "Installing the pinned CloudNativePG operator..."
  if ! helm repo add cnpg https://cloudnative-pg.github.io/charts --force-update >/dev/null; then
    log_warn "Helm could not reach the CloudNativePG chart repository. Check your network connection and rerun ./start.sh k8s."
    exit 1
  fi
  if ! helm repo update cnpg >/dev/null; then
    log_warn "Helm could not refresh the CloudNativePG chart index. Check your network connection and rerun ./start.sh k8s."
    exit 1
  fi
  if ! helm plugin list 2>/dev/null | awk '$1 == "cnpg-image-pin" { found = 1 } END { exit !found }'; then
    log_info "Installing the repository's CloudNativePG image digest pin..."
    if ! helm plugin install "$ROOT_DIR/deploy/helm-postrender"; then
      log_warn "Helm could not install the local digest pin plugin. Check Helm 4 and rerun ./start.sh k8s."
      exit 1
    fi
  fi
  if ! helm upgrade --install cnpg cnpg/cloudnative-pg \
    --version 0.29.1 \
    --namespace cnpg-system \
    --create-namespace \
    --values deploy/cnpg-values-local.yaml \
    --post-renderer cnpg-image-pin \
    --atomic \
    --wait \
    --timeout 240s; then
    log_warn "The pinned CloudNativePG operator did not become ready. Run 'helm status cnpg -n cnpg-system' for details, fix the reported issue, then retry."
    exit 1
  fi

  log_info "Applying declarative manifests (Kustomize local overlay)..."
  if ! kubectl apply -k k8s/overlays/local; then
    log_warn "Kubernetes rejected the local manifests. Check the kubectl error above and rerun ./start.sh k8s after fixing it."
    exit 1
  fi

  log_info "Waiting for PostgreSQL and its new TLS trust roots..."
  if ! kubectl -n demo wait --for=condition=Ready cluster/postgres-cluster --timeout=240s; then
    kubectl -n demo get cluster postgres-cluster -o wide || true
    log_warn "PostgreSQL did not become ready. Resolve the cluster condition shown above and rerun ./start.sh k8s. Persistent data was not deleted."
    exit 1
  fi
  log_info "Waiting for PostgreSQL to trust the new CA certificates..."
  certificate_ready=0
  for _ in {1..90}; do
    if python3 scripts/local-secrets.py verify-certificates --credentials-file "$credentials_file" >/dev/null 2>&1; then
      certificate_ready=1
      break
    fi
    sleep 2
  done
  if [ "$certificate_ready" -ne 1 ]; then
    log_warn "PostgreSQL has not confirmed the versioned CA certificates. Check 'kubectl -n demo get cluster postgres-cluster -o wide', then rerun after resolving the certificate condition."
    exit 1
  fi
  log_ok "PostgreSQL server and replication certificates trust the versioned CAs."
  if ! python3 scripts/local-secrets.py rotate-owner --credentials-file "$credentials_file"; then
    log_warn "The database owner password could not be synchronized to its versioned Secret. Check the PostgreSQL primary and retry."
    exit 1
  fi

  log_info "Checking and applying the initial snapshot schema..."
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

  log_info "Restarting the web and API pods so they use the newly built images..."
  if ! kubectl -n demo rollout restart deployment/web-app deployment/snapshot-api; then
    log_warn "The application deployments could not be restarted. Check 'kubectl -n demo get deployments' and retry."
    exit 1
  fi
  log_info "Waiting for the web dashboard and API to become ready..."
  if ! kubectl -n demo rollout status deployment/web-app --timeout=180s; then
    log_warn "The web dashboard did not become ready. Check 'kubectl -n demo describe deployment web-app' and retry."
    exit 1
  fi
  if ! kubectl -n demo rollout status deployment/snapshot-api --timeout=180s; then
    log_warn "The snapshot API did not become ready. Check 'kubectl -n demo describe deployment snapshot-api' and retry."
    exit 1
  fi

  log_info "Removing superseded local database secrets..."
  kubectl -n demo delete secret \
    datastore-credentials-v1 \
    api-datastore-credentials-v1 \
    collector-datastore-credentials-v1 \
    postgres-cluster-ca \
    --ignore-not-found >/dev/null

  COLLECTOR_JOB="collector-manual-$(date -u +%s)-$$"
  log_info "Running an initial system snapshot..."
  if ! kubectl -n demo create job --from=cronjob/collector "$COLLECTOR_JOB"; then
    log_warn "The initial collector Job could not be created. Check 'kubectl -n demo get cronjob collector' and retry."
    exit 1
  fi
  if ! kubectl -n demo wait --for=condition=complete "job/$COLLECTOR_JOB" --timeout=300s; then
    kubectl -n demo get job "$COLLECTOR_JOB" -o wide || true
    log_warn "The collector Job did not finish. Its log output was not printed. Inspect the Job with 'kubectl -n demo describe job $COLLECTOR_JOB', fix the reported issue, then rerun ./start.sh k8s."
    exit 1
  fi

  log_ok "Cluster workloads are ready and the initial snapshot was saved."
  printf "\n${GREEN}${BOLD}Dashboard:${RESET} https://takehome.localhost:8443\n"
  printf "${BOLD}Root CA certificate:${RESET} %s\n" "$local_tls_dir/ca-v2.crt"
  printf "Trust this CA in your browser or OS to remove the local HTTPS warning.\n"
  if lsof -ti tcp:8443 >/dev/null 2>&1; then
    if https_check "$local_tls_dir/ca-v2.crt" "https://takehome.localhost:8443/api/v1/overview" components; then
      log_ok "The HTTPS dashboard is already reachable at https://takehome.localhost:8443. Its existing port-forward is still running."
      exit 0
    fi
    log_warn "Port 8443 is still in use. Stop its current listener (press Ctrl+C in the existing port-forward terminal), then rerun ./start.sh k8s."
    exit 1
  fi
  printf "Keep this terminal open while you use the dashboard. Press ${BOLD}Ctrl+C${RESET} here to stop it.\n\n"
  exec kubectl --context=takehome -n demo port-forward --address=localhost service/web-app 8443:443
}

# ------------------------------------------------------------------------------
# Mode: Local Stack (Zero-Cluster Fast Start)
# ------------------------------------------------------------------------------
run_local() {
  print_banner
  log_info "Starting local developer stack (FastAPI Backend + Vite Frontend)..."

  # 1. Offer to install missing local-stack prerequisites through a known package manager.
  if ! install_missing_commands bun python3 openssl lsof; then
    exit 1
  fi

  local_tls_dir="$(get_tls_dir)"
  log_info "Preparing local HTTPS certificates..."
  if ! python3 scripts/local-tls.py ensure; then
    log_warn "Local HTTPS certificates could not be prepared. Keep the owner-only certificate files intact and resolve the reported issue."
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
  for port in 8444 5173; do
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
  log_info "Launching Unified FastAPI backend with HTTPS on 127.0.0.1:8444..."
  PORT=8444 API_HOST=127.0.0.1 \
    TLS_CERT_FILE="$local_tls_dir/api-v2.crt" TLS_KEY_FILE="$local_tls_dir/api-v2.key" \
    .venv/bin/python api/main.py &
  API_PID=$!

  # Wait briefly for API readiness
  API_READY=0
  for _ in {1..30}; do
    if https_check "$local_tls_dir/ca-v2.crt" "https://localhost:8444/healthz"; then
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
  log_info "Launching Vite web dashboard with HTTPS..."
  NODE_EXTRA_CA_CERTS="$local_tls_dir/ca-v2.crt" \
    WEB_TLS_CERT_FILE="$local_tls_dir/web-v3.crt" WEB_TLS_KEY_FILE="$local_tls_dir/web-v3.key" \
    LOG_API_PROXY_TARGET="https://localhost:8444" \
    SNAPSHOT_API_PROXY_TARGET="https://localhost:8444" \
    bun run --filter @local-k8s-demo/web dev &
  WEB_PID=$!

  # Wait for Vite dev server
  WEB_READY=0
  for _ in {1..30}; do
    if https_check "$local_tls_dir/ca-v2.crt" "https://takehome.localhost:5173/"; then
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
  printf "│  ${BOLD}Web Dashboard:${RESET}    ${CYAN}https://takehome.localhost:5173${RESET}              │\n"
  printf "│  ${BOLD}API Health:${RESET}       ${CYAN}https://localhost:8444/healthz${RESET}                 │\n"
  printf "│  ${BOLD}Root CA:${RESET}           ${CYAN}%s${RESET}\n" "$local_tls_dir/ca-v2.crt"
  printf "│  Trust the CA in your browser or OS for trusted HTTPS.              │\n"
  printf "├──────────────────────────────────────────────────────────────┤\n"
  printf "│  Press ${BOLD}Ctrl+C${RESET} in this terminal to shut down all services.     │\n"
  printf "└──────────────────────────────────────────────────────────────┘\n\n"

  # Open browser automatically on macOS if supported
  if command -v open >/dev/null 2>&1; then
    open "https://takehome.localhost:5173" 2>/dev/null || true
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
  help|-h|--help)
    print_usage
    ;;
  *)
    printf "Usage: %s [local|k8s|check]\n" "$0"
    exit 1
    ;;
esac
