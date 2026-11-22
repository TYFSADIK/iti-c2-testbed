#!/usr/bin/env bash
# Run once on each Proxmox host. Creates the VLAN-aware lab bridge and gives the
# host an out-of-band management address on every site VLAN, so Ansible and the
# chaos runner can reach all three sites even while a site router is broken.
#
#   ./proxmox/setup-network.sh            single host (option A): bridge with no physical port
#   ./proxmox/setup-network.sh enp2s0     one host per site (option B): bridge on the trunk port to the switch
#   HOST_OCTET=2 ...                      host address .2 in every site (use .3 and .4 on the other hosts)
set -euo pipefail
PORT=${1:-none}
OCT=${HOST_OCTET:-2}
IF=/etc/network/interfaces
if grep -q "^auto vmbr1" "$IF"; then
  echo "vmbr1 already defined in $IF; leaving it alone"
else
  cat >>"$IF" <<CFG

auto vmbr1
iface vmbr1 inet manual
    bridge-ports $PORT
    bridge-stp off
    bridge-fd 0
    bridge-vlan-aware yes
    bridge-vids 100 110 120 130
# out-of-band management addresses (not routed between sites)
auto vmbr1.110
iface vmbr1.110 inet static
    address 10.110.0.$OCT/24
auto vmbr1.120
iface vmbr1.120 inet static
    address 10.120.0.$OCT/24
auto vmbr1.130
iface vmbr1.130 inet static
    address 10.130.0.$OCT/24
CFG
fi
ifreload -a
ip -br addr | grep vmbr1
