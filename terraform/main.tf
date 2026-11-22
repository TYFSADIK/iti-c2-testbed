locals {
  k3s_mem  = var.lite ? 3072 : 4096
  k3s_cpu  = 2
  mgmt_mem = var.lite ? 3072 : 4096

  # name => spec. Each site: one router, three K3s nodes (A, B) or one management VM (C).
  vms = merge(
    { for s, net in var.sites : "rtr-${s}" => {
      site = s, role = "router", cpu = 1, mem = 512, disk = 8, data = 0,
      ip   = "${net.prefix}.1", vmid = var.vmid_base + index(keys(var.sites), s) * 10
    } },
    { for pair in flatten([for s in ["a", "b"] : [for i in [1, 2, 3] : { s = s, i = i }]]) :
      "k3s-${pair.s}${pair.i}" => {
        site = pair.s, role = "k3s", cpu = local.k3s_cpu, mem = local.k3s_mem, disk = 30, data = 40,
        ip   = "${var.sites[pair.s].prefix}.${10 + pair.i}", vmid = var.vmid_base + index(keys(var.sites), pair.s) * 10 + pair.i
    } },
    { "mgmt-c1" = {
      site = "c", role = "mgmt", cpu = 2, mem = local.mgmt_mem, disk = 100, data = 0,
      ip   = "${var.sites.c.prefix}.10", vmid = var.vmid_base + 21
    } },
  )
}

resource "proxmox_virtual_environment_download_file" "ubuntu" {
  for_each     = toset(distinct(values(var.site_nodes)))
  content_type = "iso"
  datastore_id = var.image_datastore
  node_name    = each.key
  url          = "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img"
  file_name    = "ubuntu-24.04-cloudimg-amd64.img"
  overwrite    = false
}

resource "proxmox_virtual_environment_vm" "vm" {
  for_each  = local.vms
  name      = each.key
  node_name = var.site_nodes[each.value.site]
  vm_id     = each.value.vmid
  tags      = ["c2lab", "site-${each.value.site}", each.value.role]
  on_boot   = true

  agent { enabled = true }
  cpu {
    cores = each.value.cpu
    type  = "host"
  }
  memory { dedicated = each.value.mem }

  disk {
    datastore_id = var.datastore
    file_id      = proxmox_virtual_environment_download_file.ubuntu[var.site_nodes[each.value.site]].id
    interface    = "scsi0"
    size         = each.value.disk
    discard      = "on"
  }
  dynamic "disk" {
    for_each = each.value.data > 0 ? [1] : []
    content {
      datastore_id = var.datastore
      interface    = "scsi1"
      size         = each.value.data
      discard      = "on"
      file_format  = "raw"
    }
  }

  # site LAN
  network_device {
    bridge  = var.bridge
    vlan_id = var.sites[each.value.site].vlan
  }
  # routers also get the inter-site transit VLAN; the site C router also an uplink
  dynamic "network_device" {
    for_each = each.value.role == "router" ? [1] : []
    content {
      bridge  = var.bridge
      vlan_id = var.transit.vlan
    }
  }
  dynamic "network_device" {
    for_each = each.key == "rtr-c" ? [1] : []
    content { bridge = var.uplink_bridge }
  }

  initialization {
    datastore_id = var.datastore
    ip_config {
      ipv4 {
        address = "${each.value.ip}/24"
        gateway = each.value.role == "router" ? null : "${var.sites[each.value.site].prefix}.1"
      }
    }
    dynamic "ip_config" {
      for_each = each.value.role == "router" ? [1] : []
      content {
        ipv4 { address = "${var.transit.prefix}.${index(keys(var.sites), each.value.site) + 1}/24" }
      }
    }
    dynamic "ip_config" {
      for_each = each.key == "rtr-c" ? [1] : []
      content {
        ipv4 { address = "dhcp" }
      }
    }
    dns { servers = ["${var.sites[each.value.site].prefix}.1"] }
    user_account {
      username = "ops"
      keys     = [var.ssh_public_key]
    }
  }

  lifecycle { ignore_changes = [initialization[0].user_account] }
}

resource "local_file" "inventory" {
  filename        = "${path.module}/../ansible/inventory.yml"
  file_permission = "0644"
  content = templatefile("${path.module}/inventory.tftpl", {
    vms     = local.vms
    sites   = var.sites
    transit = var.transit
  })
}
