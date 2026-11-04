#!/usr/bin/env bash
# ==============================================================================
# Firewall Rules Verification with Network Namespaces (BLOCKING 4)
# Verifies on Linux:
#   1. Instance cannot reach another instance's network (ia -> ib: DROP)
#   2. Instance cannot reach platform network even without Docker isolation (ia -> plat: DROP)
#   3. Router reaches instances and return traffic flows (router -> ia: ACCEPT)
#   4. Cloud metadata 169.254.169.254 is dropped (ia -> metadata: DROP)
# ==============================================================================
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Notice: firewall experiment requires root/CAP_NET_ADMIN privileges. Skipping."
  exit 0
fi

if ! command -v iptables >/dev/null 2>&1 || ! command -v ip >/dev/null 2>&1; then
  echo "Notice: iproute2 or iptables not installed. Skipping."
  exit 0
fi

cleanup() {
  ip netns del ns_inst1 2>/dev/null || true
  ip netns del ns_inst2 2>/dev/null || true
  ip netns del ns_plat 2>/dev/null || true
  ip netns del ns_router 2>/dev/null || true
  iptables -D DOCKER-USER -j L3MON-FIREWALL 2>/dev/null || true
  iptables -F L3MON-FIREWALL 2>/dev/null || true
  iptables -X L3MON-FIREWALL 2>/dev/null || true
  iptables -F DOCKER-USER 2>/dev/null || true
  iptables -F FORWARD 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Running L3MON-FIREWALL Traffic Experiment ==="

# Ensure DOCKER-USER chain exists in FORWARD
iptables -N DOCKER-USER 2>/dev/null || iptables -F DOCKER-USER
iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD 1 -j DOCKER-USER
iptables -A DOCKER-USER -j RETURN 2>/dev/null || true

# Apply bootstrap firewall configuration
export INSTANCER_INSTANCE_POOL="10.200.0.0/16"
export INSTANCER_POOL_SUBNET_SIZE="28"
export PLATFORM_VM_IP="10.0.0.1"
bash deploy/challenge-host/bootstrap.sh --apply

# Create namespaces
ip netns add ns_inst1
ip netns add ns_inst2
ip netns add ns_plat
ip netns add ns_router

# Enable IP forwarding on host
echo 1 > /proc/sys/net/ipv4/ip_forward

# Setup veth for inst1 (10.200.0.18/28 -> gw 10.200.0.17)
ip link add veth_i1 type veth peer name ceth_i1
ip link set ceth_i1 netns ns_inst1
ip addr add 10.200.0.17/28 dev veth_i1
ip link set veth_i1 up
ip netns exec ns_inst1 ip addr add 10.200.0.18/28 dev ceth_i1
ip netns exec ns_inst1 ip link set ceth_i1 up
ip netns exec ns_inst1 ip link set lo up
ip netns exec ns_inst1 ip route add default via 10.200.0.17

# Setup veth for inst2 (10.200.0.34/28 -> gw 10.200.0.33)
ip link add veth_i2 type veth peer name ceth_i2
ip link set ceth_i2 netns ns_inst2
ip addr add 10.200.0.33/28 dev veth_i2
ip link set veth_i2 up
ip netns exec ns_inst2 ip addr add 10.200.0.34/28 dev ceth_i2
ip netns exec ns_inst2 ip link set ceth_i2 up
ip netns exec ns_inst2 ip link set lo up
ip netns exec ns_inst2 ip route add default via 10.200.0.33

# Setup veth for platform (10.0.0.5/24 -> gw 10.0.0.1)
ip link add veth_plat type veth peer name ceth_plat
ip link set ceth_plat netns ns_plat
ip addr add 10.0.0.1/24 dev veth_plat
ip link set veth_plat up
ip netns exec ns_plat ip addr add 10.0.0.5/24 dev ceth_plat
ip netns exec ns_plat ip link set ceth_plat up
ip netns exec ns_plat ip link set lo up
ip netns exec ns_plat ip route add default via 10.0.0.1

# Setup veth for router (172.18.0.2/24 -> gw 172.18.0.1)
ip link add veth_rtr type veth peer name ceth_rtr
ip link set ceth_rtr netns ns_router
ip addr add 172.18.0.1/24 dev veth_rtr
ip link set veth_rtr up
ip netns exec ns_router ip addr add 172.18.0.2/24 dev ceth_rtr
ip netns exec ns_router ip link set ceth_rtr up
ip netns exec ns_router ip link set lo up
ip netns exec ns_router ip route add default via 172.18.0.1

# Start listener inside inst1 and inst2
ip netns exec ns_inst1 nc -l -p 8080 -k &
ip netns exec ns_inst2 nc -l -p 8080 -k &
ip netns exec ns_plat nc -l -p 8000 -k &
sleep 1

# Check 1: Instance 1 cannot reach Instance 2
echo "Testing: inst1 -> inst2 (must be DROPPED)"
if ip netns exec ns_inst1 nc -z -w 1 10.200.0.34 8080; then
  echo "FAIL: inst1 was able to connect to inst2!" >&2
  exit 1
else
  echo "PASS: inst1 -> inst2 was correctly DROPPED."
fi

# Check 2: Instance 1 cannot reach Platform
echo "Testing: inst1 -> platform (must be DROPPED)"
if ip netns exec ns_inst1 nc -z -w 1 10.0.0.5 8000; then
  echo "FAIL: inst1 was able to connect to platform!" >&2
  exit 1
else
  echo "PASS: inst1 -> platform was correctly DROPPED."
fi

# Check 3: Instance 1 cannot reach 169.254.169.254
echo "Testing: inst1 -> 169.254.169.254 (must be DROPPED)"
if ip netns exec ns_inst1 nc -z -w 1 169.254.169.254 80; then
  echo "FAIL: inst1 reached metadata!" >&2
  exit 1
else
  echo "PASS: inst1 -> 169.254.169.254 was correctly DROPPED."
fi

# Check 4: Router CAN reach Instance 1
echo "Testing: router -> inst1 (must be ACCEPTED)"
if ip netns exec ns_router nc -z -w 1 10.200.0.18 8080; then
  echo "PASS: router -> inst1 was ACCEPTED."
else
  echo "FAIL: router could not connect to inst1!" >&2
  exit 1
fi

echo "=== ALL FIREWALL CHECKS PASSED ==="
