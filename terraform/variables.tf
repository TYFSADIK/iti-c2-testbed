variable "proxmox_endpoint" {
  description = "Proxmox API URL, e.g. https://192.168.1.10:8006/"
  type        = string
}

variable "proxmox_api_token" {
  description = "API token 'user@realm!id=secret' (see docs/02-proxmox.md)"
  type        = string
  sensitive   = true
}

variable "proxmox_insecure" {
  description = "Accept the self-signed Proxmox certificate"
  type        = bool
  default     = true
}

variable "ssh_public_key" {
  description = "Public key installed on every VM for Ansible"
  type        = string
}

variable "bridge" {
  description = "VLAN-aware bridge that carries the site VLANs"
  type        = string
  default     = "vmbr1"
}

variable "uplink_bridge" {
  description = "Bridge with internet access, for the site C router's uplink"
  type        = string
  default     = "vmbr0"
}

variable "datastore" {
  description = "Datastore for VM disks"
  type        = string
  default     = "local-lvm"
}

variable "image_datastore" {
  description = "Datastore that holds the Ubuntu cloud image (needs the 'iso' content type)"
  type        = string
  default     = "local"
}

variable "lite" {
  description = "Smaller VMs (about 20 GB of RAM in total instead of 30 GB)"
  type        = bool
  default     = false
}

# One Proxmox node for everything, or one physical host per site (docs/01-physical.md).
variable "site_nodes" {
  description = "Proxmox node name for each site"
  type        = map(string)
  default     = { a = "pve", b = "pve", c = "pve" }
}

variable "sites" {
  description = "Site networks: VLAN on the bridge and the /24 used inside the site"
  type = map(object({
    vlan   = number
    prefix = string
  }))
  default = {
    a = { vlan = 110, prefix = "10.110.0" }
    b = { vlan = 120, prefix = "10.120.0" }
    c = { vlan = 130, prefix = "10.130.0" }
  }
}

variable "transit" {
  description = "Inter-site 'WAN' VLAN between the site routers (WireGuard runs over it)"
  type        = object({ vlan = number, prefix = string })
  default     = { vlan = 100, prefix = "10.100.0" }
}

variable "vmid_base" {
  type    = number
  default = 900
}
