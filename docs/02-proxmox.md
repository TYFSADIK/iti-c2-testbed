# 2. Create the three sites (Terraform)

Run on the Proxmox host (or any machine that can reach its API and SSH).

## API token for Terraform

```bash
pveum user add terraform@pve
pveum role add C2Lab -privs "Datastore.AllocateSpace Datastore.AllocateTemplate Datastore.Audit Pool.Allocate Sys.Audit Sys.Console Sys.Modify VM.Allocate VM.Audit VM.Clone VM.Config.CDROM VM.Config.Cloudinit VM.Config.CPU VM.Config.Disk VM.Config.HWType VM.Config.Memory VM.Config.Network VM.Config.Options VM.Migrate VM.Monitor VM.PowerMgmt SDN.Use"
pveum aclmod / -user terraform@pve -role C2Lab
pveum user token add terraform@pve c2lab --privsep 0      # copy the secret it prints
```

The provider also uploads the cloud image over SSH as root: run `ssh-add` with a
key that root on the host accepts.

## Apply

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars && nano terraform.tfvars
terraform init
terraform plan        # 10 VMs, about 29.5 GB of RAM
terraform apply       # downloads Ubuntu 24.04, creates the VMs, writes ../ansible/inventory.yml
```

| VM | Site | Address | vCPU / RAM | Disks | Role |
| --- | --- | --- | --- | --- | --- |
| rtr-a | A | 10.110.0.1, transit 10.100.0.1 | 1 / 0.5 GB | 8 GB | nftables, WireGuard, dnsmasq, WAN fault control |
| k3s-a1..a3 | A | 10.110.0.11-13 | 2 / 4 GB | 30 + 40 GB | K3s server (embedded etcd), Longhorn |
| rtr-b | B | 10.120.0.1, transit 10.100.0.2 | 1 / 0.5 GB | 8 GB | as rtr-a |
| k3s-b1..b3 | B | 10.120.0.11-13 | 2 / 4 GB | 30 + 40 GB | as site A |
| rtr-c | C | 10.130.0.1, transit 10.100.0.3, uplink DHCP | 1 / 0.5 GB | 8 GB | as rtr-a, plus NAT to the internet for every site |
| mgmt-c1 | C | 10.130.0.10 | 2 / 4 GB | 100 GB | witness and failover controller, DNS, S3 backups, Prometheus, Loki, Grafana |

`lite = true` gives 3 GB to the K3s and management VMs (about 20 GB in total).
Next: [03-deploy.md](03-deploy.md).
