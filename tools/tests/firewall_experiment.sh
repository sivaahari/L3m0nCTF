#!/usr/bin/env bash
# ==============================================================================
# Firewall Rules Verification with Network Namespaces (BLOCKING 4)
# Verifies on Linux:
#   1. Instance cannot reach another instance's network (ia -> ib: DROP)
#      Note: The pool-to-pool drop rule (-s 10.200.0.0/16 -d 10.200.0.0/16 -j DROP)
#      is redundant with the general RFC1918 drop rule (-s 10.200.0.0/16 -d 10.0.0.0/8 -j DROP)
#      when the instance pool sits within 10.0.0.0/8. Both are maintained for defense-in-depth.
#   2. Instance cannot reach platform network even without Docker isolation (ia -> plat: DROP)
#   3. Cloud metadata 169.254.169.254 in dedicated namespace is dropped across FORWARD chain (ia -> metadata: DROP)
#   4. Router reaches instances and return traffic flows across shared bridge (router -> ia: ACCEPT)
#   5. Port 8300 access to instancer agent from non-platform address is dropped (ia -> agent:8300: DROP)
#   6. Port 8300 access to instancer agent from platform VM is accepted (plat -> agent:8300: ACCEPT)
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

# The whole experiment runs inside a private network namespace (and mount namespace). It sets the FORWARD policy, adds rules and flushes chains, and it must
# never do that to the machine it runs on: on a Docker host it would cut the containers off. Nothing of it survives the end of the script.
if [[ -z "${L3MON_FW_EXPERIMENT_INNER:-}" ]]; then
  if ! command -v unshare >/dev/null 2>&1; then
    echo "Notice: unshare (util-linux) not installed. Skipping."
    exit 0
  fi
  exec unshare --net --mount --propagation private env L3MON_FW_EXPERIMENT_INNER=1 bash "$0" "$@"
fi
ip link set lo up

NC_PIDS=()

cleanup() {
  # Kill background listeners to prevent holding stdout/stderr pipes open
  if [[ ${#NC_PIDS[@]} -gt 0 ]]; then
    kill -9 "${NC_PIDS[@]}" 2>/dev/null || true
  fi
  pkill -9 -f "nc -l" 2>/dev/null || true

  ip netns del ns_inst1 2>/dev/null || true
  ip netns del ns_inst2 2>/dev/null || true
  ip netns del ns_plat 2>/dev/null || true
  ip netns del ns_router 2>/dev/null || true
  ip netns del ns_meta 2>/dev/null || true
  ip netns del ns_agent 2>/dev/null || true
  ip netns del ns_vpc 2>/dev/null || true
  ip link del veth_vpc 2>/dev/null || true
  ip link del br_inst1 2>/dev/null || true
  ip link del br_inst2 2>/dev/null || true
  ip link del br_plat 2>/dev/null || true
  ip link del veth_meta 2>/dev/null || true
  ip link del veth_agent 2>/dev/null || true
  iptables -D DOCKER-USER -j L3MON-FIREWALL 2>/dev/null || true
  iptables -F L3MON-FIREWALL 2>/dev/null || true
  iptables -X L3MON-FIREWALL 2>/dev/null || true
  iptables -F DOCKER-USER 2>/dev/null || true
  iptables -F FORWARD 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Running L3MON-FIREWALL Traffic Experiment (Real Bridge Topology) ==="

# Enable br_netfilter so bridged packets are evaluated by iptables
modprobe br_netfilter 2>/dev/null || true
if [[ -f /proc/sys/net/bridge/bridge-nf-call-iptables ]]; then
  echo 1 > /proc/sys/net/bridge/bridge-nf-call-iptables
fi
echo 1 > /proc/sys/net/ipv4/ip_forward

# Ensure DOCKER-USER chain exists in FORWARD
iptables -N DOCKER-USER 2>/dev/null || iptables -F DOCKER-USER
iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD 1 -j DOCKER-USER
iptables -A DOCKER-USER -j RETURN 2>/dev/null || true

# What Docker itself does on a real host, emulated for this experiment's bridges: the FORWARD policy is DROP and, behind DOCKER-USER, Docker accepts what leaves
# a bridge, what stays on a bridge, the replies of established connections, and the published port of a container (here the agent's 8300).
# L3MON-FIREWALL sits in DOCKER-USER, in front of all of it, so only what the firewall lets through can reach these accepts. (An earlier version of this
# experiment relied on a permissive FORWARD policy and failed on every host that runs Docker, where the policy is DROP.)
iptables -P FORWARD DROP
for br in br_inst1 br_inst2 br_plat; do
  iptables -A FORWARD -o "$br" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  iptables -A FORWARD -i "$br" ! -o "$br" -j ACCEPT
  iptables -A FORWARD -i "$br" -o "$br" -j ACCEPT
done
iptables -A FORWARD -o veth_agent -j ACCEPT

# Apply bootstrap firewall configuration
export INSTANCER_INSTANCE_POOL="10.200.0.0/16"
export INSTANCER_POOL_SUBNET_SIZE="28"
export PLATFORM_VM_IP="10.0.0.5"
export CHALLENGE_HOST_PRIVATE_IP="10.0.1.10"
bash deploy/challenge-host/bootstrap.sh --firewall-only

# Create namespaces
ip netns add ns_inst1
ip netns add ns_inst2
ip netns add ns_plat
ip netns add ns_router
ip netns add ns_meta
ip netns add ns_agent
ip netns add ns_vpc

# 1. Cloud Metadata Namespace (169.254.169.254/32 behind host routing across FORWARD chain)
ip link add veth_meta type veth peer name ceth_meta
ip link set ceth_meta netns ns_meta
ip netns exec ns_meta ip addr add 169.254.169.254/32 dev ceth_meta
ip netns exec ns_meta ip link set ceth_meta up
ip netns exec ns_meta ip link set lo up
ip addr add 169.254.169.1/30 dev veth_meta
ip link set veth_meta up
ip netns exec ns_meta ip route add 169.254.169.1 dev ceth_meta
ip netns exec ns_meta ip route add default via 169.254.169.1
ip route add 169.254.169.254/32 dev veth_meta

# 2. Instancer Agent Namespace (10.0.1.10/24 behind host routing)
ip link add veth_agent type veth peer name ceth_agent
ip link set ceth_agent netns ns_agent
ip netns exec ns_agent ip addr add 10.0.1.10/24 dev ceth_agent
ip netns exec ns_agent ip link set ceth_agent up
ip netns exec ns_agent ip link set lo up
ip addr add 10.0.1.1/24 dev veth_agent
ip link set veth_agent up
ip netns exec ns_agent ip route add default via 10.0.1.1
ip route add 10.0.1.10/32 dev veth_agent

# 2b. A third party on the VPC (10.0.2.7): not the platform, not in the instance pool. Only the port-8300 rule keeps it away from the agent.
ip link add veth_vpc type veth peer name ceth_vpc
ip link set ceth_vpc netns ns_vpc
ip netns exec ns_vpc ip addr add 10.0.2.7/24 dev ceth_vpc
ip netns exec ns_vpc ip link set ceth_vpc up
ip netns exec ns_vpc ip link set lo up
ip addr add 10.0.2.1/24 dev veth_vpc
ip link set veth_vpc up
ip netns exec ns_vpc ip route add default via 10.0.2.1

# 3. Instance 1 Network Bridge (br_inst1: 10.200.0.16/28, gw 10.200.0.17)
# Real topology: Both the challenge container and the router are on the SAME instance bridge!
ip link add br_inst1 type bridge
ip addr add 10.200.0.17/28 dev br_inst1
ip link set br_inst1 up

# Connect inst1 (10.200.0.18/28) to br_inst1
ip link add veth_i1 type veth peer name ceth_i1
ip link set ceth_i1 netns ns_inst1
ip link set veth_i1 master br_inst1
ip link set veth_i1 up
ip netns exec ns_inst1 ip addr add 10.200.0.18/28 dev ceth_i1
ip netns exec ns_inst1 ip link set ceth_i1 up
ip netns exec ns_inst1 ip link set lo up
ip netns exec ns_inst1 ip route add default via 10.200.0.17

# Connect router (10.200.0.19/28) to br_inst1 (shared bridge with inst1)
ip link add veth_rtr type veth peer name ceth_rtr
ip link set ceth_rtr netns ns_router
ip link set veth_rtr master br_inst1
ip link set veth_rtr up
ip netns exec ns_router ip addr add 10.200.0.19/28 dev ceth_rtr
ip netns exec ns_router ip link set ceth_rtr up
ip netns exec ns_router ip link set lo up
ip netns exec ns_router ip route add default via 10.200.0.17

# 4. Instance 2 Network Bridge (br_inst2: 10.200.0.32/28, gw 10.200.0.33)
ip link add br_inst2 type bridge
ip addr add 10.200.0.33/28 dev br_inst2
ip link set br_inst2 up

# Connect inst2 (10.200.0.34/28) to br_inst2
ip link add veth_i2 type veth peer name ceth_i2
ip link set ceth_i2 netns ns_inst2
ip link set veth_i2 master br_inst2
ip link set veth_i2 up
ip netns exec ns_inst2 ip addr add 10.200.0.34/28 dev ceth_i2
ip netns exec ns_inst2 ip link set ceth_i2 up
ip netns exec ns_inst2 ip link set lo up
ip netns exec ns_inst2 ip route add default via 10.200.0.33

# 5. Platform Network Bridge (br_plat: 10.0.0.0/24, gw 10.0.0.1)
ip link add br_plat type bridge
ip addr add 10.0.0.1/24 dev br_plat
ip link set br_plat up

# Connect plat (10.0.0.5/24) to br_plat
ip link add veth_plat type veth peer name ceth_plat
ip link set ceth_plat netns ns_plat
ip link set veth_plat master br_plat
ip link set veth_plat up
ip netns exec ns_plat ip addr add 10.0.0.5/24 dev ceth_plat
ip netns exec ns_plat ip link set ceth_plat up
ip netns exec ns_plat ip link set lo up
ip netns exec ns_plat ip route add default via 10.0.0.1

# Start listeners with stdout and stderr sent to /dev/null
ip netns exec ns_inst1 nc -l -p 8080 -k >/dev/null 2>&1 &
NC_PIDS+=($!)
ip netns exec ns_inst2 nc -l -p 8080 -k >/dev/null 2>&1 &
NC_PIDS+=($!)
ip netns exec ns_plat nc -l -p 8000 -k >/dev/null 2>&1 &
NC_PIDS+=($!)
ip netns exec ns_meta nc -l -p 80 -k >/dev/null 2>&1 &
NC_PIDS+=($!)
ip netns exec ns_agent nc -l -p 8300 -k >/dev/null 2>&1 &
NC_PIDS+=($!)
sleep 1

# Check 1: Instance 1 cannot reach Instance 2 (inter-instance cross-talk dropped)
echo "Testing: inst1 -> inst2 (must be DROPPED)"
if ip netns exec ns_inst1 nc -z -w 1 10.200.0.34 8080; then
  echo "FAIL: inst1 was able to connect to inst2!" >&2
  exit 1
else
  echo "PASS: inst1 -> inst2 was correctly DROPPED."
fi

# Check 2: Instance 1 cannot reach Platform (VPC RFC1918 dropped)
echo "Testing: inst1 -> platform (must be DROPPED)"
if ip netns exec ns_inst1 nc -z -w 1 10.0.0.5 8000; then
  echo "FAIL: inst1 was able to connect to platform!" >&2
  exit 1
else
  echo "PASS: inst1 -> platform was correctly DROPPED."
fi

# Check 3: Instance 1 cannot reach Metadata across FORWARD chain
echo "Testing: inst1 -> 169.254.169.254 (must be DROPPED)"
if ip netns exec ns_inst1 nc -z -w 1 169.254.169.254 80; then
  echo "FAIL: inst1 reached metadata!" >&2
  exit 1
else
  echo "PASS: inst1 -> 169.254.169.254 was correctly DROPPED."
fi

# Check 4: Router CAN reach Instance 1 (intra-bridge physdev allowed)
echo "Testing: router -> inst1 (must be ACCEPTED)"
if ip netns exec ns_router nc -z -w 1 10.200.0.18 8080; then
  echo "PASS: router -> inst1 was ACCEPTED."
else
  echo "FAIL: router could not connect to inst1!" >&2
  exit 1
fi

# Check 5: Instance 1 cannot reach Agent port 8300
echo "Testing: inst1 -> agent:8300 (must be DROPPED)"
if ip netns exec ns_inst1 nc -z -w 1 10.0.1.10 8300; then
  echo "FAIL: inst1 was able to connect to agent port 8300!" >&2
  exit 1
else
  echo "PASS: inst1 -> agent:8300 was correctly DROPPED."
fi

# Check 5b: a third party on the VPC cannot reach the agent port 8300 (only the platform may). Removing the 8300 rule makes this fail.
echo "Testing: other VPC host (not the platform, not an instance) -> agent:8300 (must be DROPPED)"
if ip netns exec ns_vpc nc -z -w 1 10.0.1.10 8300; then
  echo "FAIL: a third party on the VPC was able to connect to agent port 8300!" >&2
  exit 1
else
  echo "PASS: other VPC host -> agent:8300 was correctly DROPPED."
fi

# Check 6: Platform CAN reach Agent port 8300
echo "Testing: platform -> agent:8300 (must be ACCEPTED)"
if ip netns exec ns_plat nc -z -w 1 10.0.1.10 8300; then
  echo "PASS: platform -> agent:8300 was ACCEPTED."
else
  echo "FAIL: platform was unable to connect to agent port 8300!" >&2
  exit 1
fi

echo "=== ALL FIREWALL CHECKS PASSED ==="

