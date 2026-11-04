#!/usr/bin/env bash
# ==============================================================================
# L3m0nCTF Challenge Host Bootstrap Script (SP4 / F4.2)
#
# Configures a Debian 12 / Ubuntu 22.04 / 24.04 Linux VM as an isolated
# challenge container host.
#
# Safety & Idempotency:
# - Default mode is --dry-run (prints planned actions, executes NO modifying commands).
# - Use --apply to execute changes on the host.
# - DOCKER-USER rules check with iptables -C before adding rules to prevent duplicates.
# - Unifies daemon.json default-address-pools and INSTANCER_INSTANCE_POOL from one variable.
# ==============================================================================
set -euo pipefail

MODE="dry-run"
if [[ $# -gt 0 ]]; then
  case "$1" in
    --apply)
      MODE="apply"
      ;;
    --dry-run)
      MODE="dry-run"
      ;;
    --help|-h)
      echo "Usage: $0 [--dry-run|--apply]"
      echo "  --dry-run (default): Inspect actions without applying changes"
      echo "  --apply:             Apply configuration, install Docker, configure firewall"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--dry-run|--apply]" >&2
      exit 1
      ;;
  esac
fi

# Configuration parameters (written from unified variables)
INSTANCE_POOL="${INSTANCER_INSTANCE_POOL:-10.200.0.0/16}"
POOL_SUBNET_SIZE="${INSTANCER_POOL_SUBNET_SIZE:-28}"
PLATFORM_VM_IP="${PLATFORM_VM_IP:-10.0.0.1}"
SERVICE_USER="l3mon"
CONFIG_DIR="/etc/l3mon"
MANIFEST_FILE="${CONFIG_DIR}/challenges.toml"

echo "=== L3m0nCTF Challenge Host Bootstrap ==="
echo "Mode:                    ${MODE}"
echo "Instance Network Pool:   ${INSTANCE_POOL} (subnets: /${POOL_SUBNET_SIZE})"
echo "Platform VM Allowed IP:  ${PLATFORM_VM_IP}"
echo "=========================================="

# Execution helpers
run_cmd() {
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] $*"
  else
    echo "[APPLY] $*"
    "$@"
  fi
}

write_file() {
  local target_path="$1"
  local content="$2"
  local mode="$3"

  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] Would write to ${target_path} (mode ${mode}):"
    echo "${content}"
  else
    echo "[APPLY] Writing ${target_path} (mode ${mode})"
    echo "${content}" > "${target_path}"
    chmod "${mode}" "${target_path}"
  fi
}

# 1. Verify OS compatibility
check_os() {
  echo "--- [1/7] Checking OS environment ---"
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    echo "Detected OS: ${NAME:-Linux} ${VERSION_ID:-}"
  else
    echo "Warning: /etc/os-release not found. Proceeding with caution."
  fi
}

# 2. Check time synchronization
check_time_sync() {
  echo "--- [2/7] Checking time synchronization ---"
  if command -v timedatectl >/dev/null 2>&1; then
    run_cmd timedatectl status
  elif command -v chronyc >/dev/null 2>&1; then
    run_cmd chronyc tracking
  else
    echo "Notice: Neither timedatectl nor chrony found. Ensure NTP/systemd-timesyncd is active."
  fi
}

# 3. Apply Kernel Limits & Sysctls
configure_sysctls() {
  echo "--- [3/7] Configuring Kernel Limits & Sysctls ---"
  local sysctl_file="${L3MON_SYSCTL_CONF:-/etc/sysctl.d/99-l3mon.conf}"
  local sysctl_content
  sysctl_content=$(cat <<EOF
# L3m0nCTF Challenge Host Sysctls
vm.max_map_count = 262144
fs.file-max = 2097152
net.ipv4.ip_forward = 1
EOF
)
  write_file "${sysctl_file}" "${sysctl_content}" "0644"
  run_cmd sysctl --system
}

# 4. Service user and isolated directories
configure_user_and_dirs() {
  echo "--- [4/7] Setting up Service User and Directories ---"
  if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    run_cmd useradd -r -s /usr/sbin/nologin "${SERVICE_USER}"
  fi

  run_cmd mkdir -p "${CONFIG_DIR}"
  run_cmd chmod 0700 "${CONFIG_DIR}"
  run_cmd chown "${SERVICE_USER}:${SERVICE_USER}" "${CONFIG_DIR}"

  if [[ ! -f "${MANIFEST_FILE}" ]]; then
    write_file "${MANIFEST_FILE}" "# L3m0nCTF challenge manifest placeholder" "0600"
    run_cmd chown "${SERVICE_USER}:${SERVICE_USER}" "${MANIFEST_FILE}"
  fi
}

# 5. Docker daemon configuration (/etc/docker/daemon.json)
configure_docker_daemon() {
  echo "--- [5/7] Configuring Docker Daemon (Small Subnet Pools & Log Limits) ---"
  local daemon_file="${L3MON_DAEMON_JSON:-/etc/docker/daemon.json}"
  local daemon_dir
  daemon_dir="$(dirname "${daemon_file}")"
  run_cmd mkdir -p "${daemon_dir}"

  local daemon_json
  daemon_json=$(cat <<EOF
{
  "default-address-pools": [
    {
      "base": "${INSTANCE_POOL}",
      "size": ${POOL_SUBNET_SIZE}
    }
  ],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "no-new-privileges": true,
  "live-restore": true
}
EOF
)
  write_file "${daemon_file}" "${daemon_json}" "0644"
}

# 6. DOCKER-USER and INPUT Firewall Rules
configure_firewall() {
  echo "--- [6/7] Configuring Firewall (DOCKER-USER Isolation & Cloud Rules) ---"

  # Rule 1: Always drop container access to cloud metadata address 169.254.169.254
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -C DOCKER-USER -d 169.254.169.254 -j DROP || iptables -I DOCKER-USER 1 -d 169.254.169.254 -j DROP"
  else
    iptables -C DOCKER-USER -d 169.254.169.254 -j DROP 2>/dev/null || \
      iptables -I DOCKER-USER 1 -d 169.254.169.254 -j DROP
  fi

  # Rule 2: Allow established and related traffic
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -C DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT || iptables -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
  else
    iptables -C DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
      iptables -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  fi

  # Rule 3: Allow router relay traffic into instance pool
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -C DOCKER-USER -d ${INSTANCE_POOL} -j ACCEPT || iptables -A DOCKER-USER -d ${INSTANCE_POOL} -j ACCEPT"
  else
    iptables -C DOCKER-USER -d "${INSTANCE_POOL}" -j ACCEPT 2>/dev/null || \
      iptables -A DOCKER-USER -d "${INSTANCE_POOL}" -j ACCEPT
  fi

  # Rule 4: Drop any instance pool outbound traffic to private RFC1918 VPC networks
  for vpc_range in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
    if [[ "${MODE}" == "dry-run" ]]; then
      echo "[DRY-RUN] iptables -C DOCKER-USER -s ${INSTANCE_POOL} -d ${vpc_range} -j DROP || iptables -A DOCKER-USER -s ${INSTANCE_POOL} -d ${vpc_range} -j DROP"
    else
      iptables -C DOCKER-USER -s "${INSTANCE_POOL}" -d "${vpc_range}" -j DROP 2>/dev/null || \
        iptables -A DOCKER-USER -s "${INSTANCE_POOL}" -d "${vpc_range}" -j DROP
    fi
  done

  # Rule 5: Restrict agent port 8300 to platform VM only
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -C INPUT -p tcp --dport 8300 -s ${PLATFORM_VM_IP} -j ACCEPT || iptables -I INPUT 1 -p tcp --dport 8300 -s ${PLATFORM_VM_IP} -j ACCEPT"
    echo "[DRY-RUN] iptables -C INPUT -p tcp --dport 8300 -j DROP || iptables -A INPUT -p tcp --dport 8300 -j DROP"
  else
    iptables -C INPUT -p tcp --dport 8300 -s "${PLATFORM_VM_IP}" -j ACCEPT 2>/dev/null || \
      iptables -I INPUT 1 -p tcp --dport 8300 -s "${PLATFORM_VM_IP}" -j ACCEPT
    iptables -C INPUT -p tcp --dport 8300 -j DROP 2>/dev/null || \
      iptables -A INPUT -p tcp --dport 8300 -j DROP
  fi
}

# 7. Next Steps Output
print_next_steps() {
  echo "--- [7/7] Bootstrap Complete ---"
  echo "Next steps on challenge host:"
  echo "1. Place deployment tokens in /etc/l3mon/secrets (mode 0600, root-only)."
  echo "2. Populate /etc/l3mon/challenges.toml with challenge manifests."
  echo "3. Run: docker compose -f deploy/compose/compose.challenge-host.yml up -d"
  echo "4. Add host to platform hosts.json with public_tcp_host and router_url."
}

# Main flow
check_os
check_time_sync
configure_sysctls
configure_user_and_dirs
configure_docker_daemon
configure_firewall
print_next_steps
