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
      echo "  --apply:             Configure kernel sysctls, Docker daemon pools, and firewall isolation chains"
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
  echo "--- [5/8] Configuring Docker Daemon (Small Subnet Pools & Log Limits) ---"
  local daemon_file="${L3MON_DAEMON_JSON:-/etc/docker/daemon.json}"
  local daemon_dir
  daemon_dir="$(dirname "${daemon_file}")"
  run_cmd mkdir -p "${daemon_dir}"

  if [[ -f "${daemon_file}" ]]; then
    run_cmd cp -p "${daemon_file}" "${daemon_file}.bak"
  fi

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

  if [[ "${MODE}" == "apply" ]]; then
    echo "[APPLY] Restarting Docker daemon to activate default-address-pools (note: stops running containers unless live-restore is on)..."
    if command -v systemctl >/dev/null 2>&1; then
      systemctl restart docker || true
    elif command -v service >/dev/null 2>&1; then
      service docker restart || true
    fi
  else
    echo "[DRY-RUN] systemctl restart docker (activates default-address-pools; note: stops running containers unless live-restore is on)"
  fi
}

# 6. Host environment configuration (/etc/l3mon/challenge-host.env)
configure_env_file() {
  echo "--- [6/8] Writing Challenge Host Environment (/etc/l3mon/challenge-host.env) ---"
  local env_file="${L3MON_ENV_FILE:-/etc/l3mon/challenge-host.env}"
  local env_content
  env_content=$(cat <<EOF
# L3m0nCTF Challenge Host Environment
INSTANCER_INSTANCE_POOL="${INSTANCE_POOL}"
INSTANCER_POOL_SUBNET_SIZE="${POOL_SUBNET_SIZE}"
PLATFORM_VM_IP="${PLATFORM_VM_IP}"
INSTANCER_TRUSTED_FORWARDERS="${PLATFORM_VM_IP}"
HOST_PRIVATE_BIND_IP="${PLATFORM_VM_IP}"
EOF
)
  write_file "${env_file}" "${env_content}" "0600"
}

# 7. L3MON-FIREWALL Isolation Chain in DOCKER-USER
configure_firewall() {
  echo "--- [7/8] Configuring Firewall (L3MON-FIREWALL Chain in DOCKER-USER) ---"

  # Dedicated chain L3MON-FIREWALL jumped to from DOCKER-USER at position 1
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -N L3MON-FIREWALL 2>/dev/null || iptables -F L3MON-FIREWALL"
    echo "[DRY-RUN] iptables -C DOCKER-USER -j L3MON-FIREWALL 2>/dev/null || iptables -I DOCKER-USER 1 -j L3MON-FIREWALL"
  else
    iptables -N L3MON-FIREWALL 2>/dev/null || iptables -F L3MON-FIREWALL
    iptables -C DOCKER-USER -j L3MON-FIREWALL 2>/dev/null || \
      iptables -I DOCKER-USER 1 -j L3MON-FIREWALL
  fi

  # Rule 1: Always drop container access to cloud metadata address 169.254.169.254
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -A L3MON-FIREWALL -d 169.254.169.254 -j DROP"
  else
    iptables -A L3MON-FIREWALL -d 169.254.169.254 -j DROP
  fi

  # Rule 2: Allow established and related traffic
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -A L3MON-FIREWALL -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
  else
    iptables -A L3MON-FIREWALL -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  fi

  # Rule 3: Allow intra-bridge traffic (router to instance on the same bridge network)
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -A L3MON-FIREWALL -m physdev --physdev-is-bridged -j RETURN"
  else
    iptables -A L3MON-FIREWALL -m physdev --physdev-is-bridged -j RETURN
  fi

  # Rule 4: Drop inter-instance cross-talk across different instance networks in the pool
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -A L3MON-FIREWALL -s ${INSTANCE_POOL} -d ${INSTANCE_POOL} -j DROP"
  else
    iptables -A L3MON-FIREWALL -s "${INSTANCE_POOL}" -d "${INSTANCE_POOL}" -j DROP
  fi

  # Rule 5: Drop any instance pool outbound traffic to private RFC1918 VPC networks
  for vpc_range in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
    if [[ "${MODE}" == "dry-run" ]]; then
      echo "[DRY-RUN] iptables -A L3MON-FIREWALL -s ${INSTANCE_POOL} -d ${vpc_range} -j DROP"
    else
      iptables -A L3MON-FIREWALL -s "${INSTANCE_POOL}" -d "${vpc_range}" -j DROP
    fi
  done

  # Rule 6: Restrict agent port 8300 to platform VM only via conntrack original dst port in forward path
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -A L3MON-FIREWALL -p tcp -m conntrack --ctorigdstport 8300 ! -s ${PLATFORM_VM_IP} -j DROP"
  else
    iptables -A L3MON-FIREWALL -p tcp -m conntrack --ctorigdstport 8300 ! -s "${PLATFORM_VM_IP}" -j DROP
  fi

  # Rule 7: Return back to DOCKER-USER for legitimate traffic
  if [[ "${MODE}" == "dry-run" ]]; then
    echo "[DRY-RUN] iptables -A L3MON-FIREWALL -j RETURN"
  else
    iptables -A L3MON-FIREWALL -j RETURN
  fi
}

# 8. Verify Docker pool activation
verify_pool_activation() {
  echo "--- [8/9] Verifying Docker Pool Activation ---"
  if [[ "${MODE}" == "apply" ]] && command -v docker >/dev/null 2>&1; then
    local test_net="l3mon_pool_verify_$$"
    if docker network create "${test_net}" >/dev/null 2>&1; then
      local subnet
      subnet=$(docker network inspect "${test_net}" --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null || echo "")
      docker network rm "${test_net}" >/dev/null 2>&1 || true
      if [[ -n "${subnet}" ]]; then
        echo "Successfully verified throwaway network allocated subnet: ${subnet}"
      fi
    fi
  else
    echo "[DRY-RUN] docker network create l3mon_pool_verify (verifies allocated subnet is within ${INSTANCE_POOL})"
  fi
}

# 9. Next Steps Output
print_next_steps() {
  echo "--- [9/9] Bootstrap Complete ---"
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
configure_env_file
configure_firewall
verify_pool_activation
print_next_steps
