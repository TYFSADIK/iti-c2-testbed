# Architecture reference

## Sites

| Site | Role | VLAN | Subnet | Machines |
| --- | --- | --- | --- | --- |
| A | primary | 110 | 10.110.0.0/24 | rtr-a, k3s-a1..a3 |
| B | alternate (warm standby) | 120 | 10.120.0.0/24 | rtr-b, k3s-b1..b3 |
| C | management, witness, backups | 130 | 10.130.0.0/24 | rtr-c, mgmt-c1 |
| transit | inter-site "WAN" | 100 | 10.100.0.0/24 | the three routers |
| WireGuard | encrypted mesh over transit | - | 10.99.0.0/24 | the three routers |

## Fixed addresses

| Address | What |
| --- | --- |
| x.x.x.1 | site router (gateway, DNS forwarder) |
| x.x.x.2-4 | Proxmox hosts, out-of-band (not routed) |
| 10.110.0.80 / 10.120.0.80 | C2 Board service at A / B (MetalLB) |
| 10.110.0.81 | site A database endpoint for replication to B (MetalLB) |
| 10.130.0.10 | mgmt-c1: DNS :53, S3 :9000, witness :8090, Prometheus :9090, Loki :3100, Grafana :3000 |

## Layers of redundancy

| Failure | Protected by | Recovery |
| --- | --- | --- |
| app pod | 2 replicas, Kubernetes restarts | none visible |
| database instance | CloudNativePG: 3 instances at A, one synchronous, automatic in-site failover | seconds |
| K3s node | 3-server embedded etcd per site; Longhorn 3 replicas | node declared dead, pods rescheduled |
| disk | Longhorn replica on another node, rebuild | none visible |
| WAN degradation | nothing to do; measured | none |
| whole site | replica cluster at B streaming from A; witness + controller at C; DNS switch | `FAIL_AFTER` + promotion |
| broken standby | controller rebuilds a non-stale standby from the active site after `RESEED_AFTER` | minutes, no service impact |
| data corruption / both sites | base backups + continuous WAL in S3 at C; Longhorn and Velero backups | restore from backup |

## Split-brain protection

```
app at site A  ── renews write lease every 2 s ──>  witness (site C)
                  no lease for 10 s -> refuses writes
controller     ── fails over only after 90 s ──>   by then A has fenced itself
```

The rule `FAIL_AFTER >= LEASE_TTL + LEASE_POLL + 3` is enforced at start-up.
Consequence: if site C is lost, writes stop everywhere after 10 s and resume
when it returns. A three-member witness (one small VM per site, majority vote)
would remove that single point; it is on the roadmap.

## Data flow

```
client ─DNS c2.lab (site C CoreDNS, TTL 2 s)─> active site VIP ─> C2 Board ─> CloudNativePG primary
site A primary ─streaming replication over WireGuard─> site B replica cluster
site A primary ─WAL archive + hourly base backup─> S3 at site C
every node ─Alloy─> Prometheus (metrics, label site) and Loki (logs) at site C
controller ─/healthz of each site every 1 s─> decides ─> lease, DNS, promote (kubectl patch)
```

## Ports between sites (nftables)

Sites reach each other only through `wg0`. Inbound on a router: its own LAN,
the WireGuard subnet, UDP 51820, ICMP. Site C is the only exit to the internet.
