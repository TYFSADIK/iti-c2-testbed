# 3. Configure everything (Ansible)

From the machine that ran Terraform, with `kubectl`, `helm` and `python3`
installed and the out-of-band addresses reachable (the Proxmox host has them).

```bash
./tools/gen-secrets.sh                                  # secrets/lab.env, once
cd ansible
ansible-galaxy collection install -r requirements.yml
ansible-playbook site.yml                               # 20-40 minutes
```

| Play | Result |
| --- | --- |
| common | qemu agent, chrony, iSCSI tools for Longhorn, UTC |
| router | WireGuard full mesh between the three routers over the transit VLAN; nftables default deny; site A and B reach the internet only through site C; dnsmasq sends `*.lab` to site C's DNS; `wan-impair` for fault injection |
| k3s | one three-server K3s cluster per site with embedded etcd (survives one node down); Longhorn data disk on `/var/lib/longhorn`; kubeconfigs in `secrets/kube/` |
| mgmt | Docker and the site C stack: witness and failover controller, CoreDNS (`c2.lab`), SeaweedFS S3, Prometheus, Loki, Grafana, prober |
| platform | per site: MetalLB (pool .80-.89), Longhorn (3 replicas, nightly backup to S3), kube-state-metrics and Grafana Alloy (metrics to Prometheus, logs to Loki, labelled by site), Velero (hourly object backups), CloudNativePG; site A primary database (3 instances) and the C2 Board; site B replica database (2 instances) streaming from A, and the C2 Board as standby |

## Check

```bash
curl http://10.110.0.80/healthz    # {"site": "a", "db": "primary", "writable": true}
curl http://10.120.0.80/healthz    # {"site": "b", "db": "standby", "writable": false}  (HTTP 503)
curl http://10.130.0.10:8090/state # active site a, both probes
dig @10.130.0.10 c2.lab +short     # 10.110.0.80
```

Grafana: `http://10.130.0.10:3000` (admin / `GRAFANA_ADMIN_PASSWORD` from
`secrets/lab.env`), dashboard *C2 Availability and Failover*.

## The application image

`APP_IMAGE` defaults to `ghcr.io/tyfsadik/iti-c2-testbed:latest`, built by the
repository's GitHub Actions workflow for amd64 and arm64. To build your own:
`docker build -t <registry>/c2-services:1 services && docker push ...` and set
`app_image` in `ansible/group_vars/all.yml`.
