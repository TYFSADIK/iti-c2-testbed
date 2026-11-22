# 6. Local rehearsal (no Proxmox needed)

`local/rehearse.sh` builds a small copy of the design on one machine with
Docker and k3d, runs the failure scenarios and writes `results/rehearsal/report.md`.
It is how the failover logic, the fencing and the measurement tools were tested
before any VM exists.

```bash
./local/rehearse.sh up      # about 10 minutes, about 5 GB of RAM
./local/rehearse.sh test    # about 20 minutes
./local/rehearse.sh down
```

| Piece | Rehearsal | Proxmox build |
| --- | --- | --- |
| Sites | Docker network, k3d clusters (A: 3 nodes, B: 2 nodes) | VLANs, VMs, routers |
| Database | CloudNativePG 1.24, A: 2 instances, B: 1-instance replica cluster | A: 3 instances, B: 2 |
| Storage | k3s local-path | Longhorn, 3 replicas |
| Load balancer | k3s service load balancer | MetalLB |
| Inter-site link | Docker bridge; faults with `tc netem` and `docker network disconnect` | WireGuard over the transit VLAN; `wan-impair` |
| Witness, controller, DNS, S3, Prometheus, Grafana, Loki, prober | same images and compose file | same |
| Node loss | `docker stop` of a k3d node | `qm stop` (or pulled power, option B) |

Not rehearsed: Longhorn, MetalLB, WireGuard, Velero, Alloy. Their configuration
is checked offline (Helm charts rendered with the values and validated against
the Kubernetes schemas; the Alloy config parsed by Alloy itself).
