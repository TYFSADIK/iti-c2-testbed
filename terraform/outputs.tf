output "vms" {
  value = { for k, v in local.vms : k => { ip = v.ip, site = v.site, vmid = v.vmid, node = var.site_nodes[v.site] } }
}

output "total_ram_gb" {
  value = sum([for v in local.vms : v.mem]) / 1024
}
