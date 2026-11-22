# 1. Physical setup: your part

Two ways to build the three sites. Start with option A; option B makes the
failures physical (pulled power cords and cables instead of commands).

## Option A: one Proxmox host (start here)

| Need | Minimum | Notes |
| --- | --- | --- |
| Proxmox VE 8 host | 8 cores, **32 GB RAM** (24 GB with `lite = true`), 500 GB SSD | 10 VMs, 29.5 GB in the full build |
| Network | one port to your home network | the three sites are VLANs on an internal bridge |

Steps:

1. Install Proxmox VE 8 and update it (`apt update && apt full-upgrade`).
2. Copy this repository to the host: `git clone https://github.com/TYFSADIK/iti-c2-testbed /root/iti-c2-testbed`.
3. Create the lab bridge with no physical port and the out-of-band addresses:
   `./proxmox/setup-network.sh`.
4. Continue with [02-proxmox.md](02-proxmox.md).

## Option B: one physical host per site

Three small machines, one per site, joined by a managed switch. Each site
can then lose real power and real cables.

| Item | Example | Qty | About (CAD) |
| --- | --- | --- | --- |
| Mini PC, 6+ cores, 16 GB RAM, 500 GB NVMe | Intel N305 or Ryzen 5 class, 2 Ethernet ports | 3 | 350-500 each |
| Managed switch with 802.1Q VLANs | TP-Link TL-SG108E, Netgear GS308E | 1 | 40-60 |
| Short Cat6 patch cables | 0.5-1 m | 6 | 15 |
| Smart plugs with local control (optional) | Shelly Plug or any Tasmota plug | 3 | 25 each |
| Small UPS for the switch (optional) | 400-600 VA | 1 | 90 |

Wiring:

```
                home router (internet)
                       │
          ┌────────────┴─────────────┐  switch port 1: untagged home LAN (VLAN 1)
          │     managed switch       │  ports 2-4: trunk, tagged VLANs 100,110,120,130
          └──┬──────────┬─────────┬──┘
     port 2  │   port 3 │  port 4 │
       pve1 (site A)  pve2 (site B)  pve3 (site C)
       port 1 of each mini PC: home LAN (Proxmox web UI)
       port 2 of each mini PC: trunk to the switch (vmbr1)
```

1. **Switch VLANs.** On a TP-Link Easy Smart switch: *VLAN > 802.1Q VLAN*,
   enable, then add VLANs 100, 110, 120 and 130, each **tagged** on ports 2, 3
   and 4 and not a member of port 1. Netgear: *VLAN > Advanced 802.1Q*.
2. **Proxmox on each host,** named `pve1`, `pve2`, `pve3`. Join them in one cluster
   (*Datacenter > Cluster > Create* on pve1, *Join* on the others) so Terraform
   sees all three, but **do not** use Proxmox HA or Ceph: the point is that each
   site survives on its own.
3. **Lab bridge on each host** on the trunk port, with a different out-of-band
   address per host:

   ```bash
   HOST_OCTET=2 ./proxmox/setup-network.sh enp2s0   # pve1
   HOST_OCTET=3 ./proxmox/setup-network.sh enp2s0   # pve2
   HOST_OCTET=4 ./proxmox/setup-network.sh enp2s0   # pve3
   ```

4. In `terraform/terraform.tfvars`: `site_nodes = { a = "pve1", b = "pve2", c = "pve3" }`.
5. **Label everything:** cables and plugs "SITE A", "SITE B", "SITE C". During
   the tests you will pull them by hand.

Physical tests that option B adds (run the prober first, see
[04-testing.md](04-testing.md), and note the time you pull or plug):

| Test | How | What should happen |
| --- | --- | --- |
| Site A power loss | switch off site A's smart plug, or pull its power cord | failover to site B after `FAIL_AFTER`, no write accepted twice |
| Site A WAN cut | unplug site A's trunk cable from the switch | same as power loss, then site A fences itself |
| Switch reboot | power-cycle the switch | short outage at every site; no failover if under `FAIL_AFTER` |
| Site C (witness) loss | switch off site C | writes stop about 10 s later at every site (no site can renew its write lease) and resume when site C is back: the price of never having two writers |

## Safety

Everything runs on your own machines and network. The chaos scripts only touch
VMs tagged `c2lab`. Keep the lab VLANs off your home network: only site C's
router has an uplink.
