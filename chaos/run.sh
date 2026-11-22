#!/usr/bin/env bash
# Failure scenarios on the Proxmox build. Run on the Proxmox node (it has the qm
# command and out-of-band addresses on every site VLAN). Each scenario writes a
# marker; tools/report.py turns the prober CSV and the markers into numbers.
#
#   ./chaos/run.sh all                 every scenario in order (about 45 minutes)
#   ./chaos/run.sh node-off            one scenario
#   ./chaos/run.sh report              fetch the prober data from site C and write results/proxmox/report.md
#
# Scenarios
#   app-pod        kill one C2 Board pod at site A
#   db-primary     kill the database primary pod at site A (in-site database failover)
#   node-off       hard power-off of the K3s node running the site A database primary
#   disk-fail      fail the Longhorn data disk of one site A node (SCSI device offline)
#   wan-degrade    80 ms delay and 2% loss on site A's WAN for 5 minutes
#   wan-cut        site A cut from the other sites for 3 minutes, then restored (partition)
#   site-loss      every site A VM powered off (failover to site B)
#   restore        rebuild the database from the S3 backup at site B (restore time)
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=results/proxmox
mkdir -p "$OUT"
KA="kubectl --kubeconfig secrets/kube/site-a.yaml"
KB="kubectl --kubeconfig secrets/kube/site-b.yaml"
SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
VMID_BASE=${VMID_BASE:-900}
SITE_A_VMS="$((VMID_BASE + 0)) $((VMID_BASE + 1)) $((VMID_BASE + 2)) $((VMID_BASE + 3))"

now() { date +%s.%N | cut -c1-14; }
mark() { printf '{"scenario": "%s", "fault": %s, "end": %s, "check_loss": true}\n' "$1" "$2" "$3" >>"$OUT/markers.jsonl"; echo "== $1 done"; }
primary_pod() { $KA -n c2 get pods -l cnpg.io/cluster=c2db,cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'; }
vmid_of() { qm list | awk -v n="$1" '$2 == n {print $1}'; }

app-pod() { t=$(now); $KA -n c2 delete pod "$($KA -n c2 get pod -l app=c2board -o jsonpath='{.items[0].metadata.name}')" --wait=false; sleep 60; mark "app pod killed" "$t" "$(now)"; }
db-primary() { t=$(now); $KA -n c2 delete pod "$(primary_pod)" --wait=false; sleep 120; mark "database primary pod killed" "$t" "$(now)"; }
node-off() {
  node=$($KA -n c2 get pod "$(primary_pod)" -o jsonpath='{.spec.nodeName}'); id=$(vmid_of "$node")
  t=$(now); qm stop "$id" --skiplock 1; sleep 240; mark "node powered off ($node)" "$t" "$(now)"
  qm start "$id"; sleep 120
}
disk-fail() {
  node=k3s-a2; ip=$($KA get node $node -o jsonpath='{.status.addresses[0].address}')
  t=$(now); $SSH "ops@$ip" 'echo offline | sudo tee /sys/block/sdb/device/state'
  sleep 300; mark "Longhorn disk failed ($node)" "$t" "$(now)"
  $SSH "ops@$ip" 'echo running | sudo tee /sys/block/sdb/device/state'; sleep 120
}
wan-degrade() { t=$(now); $SSH ops@10.110.0.1 'sudo wan-impair degrade 80 2'; sleep 300; $SSH ops@10.110.0.1 'sudo wan-impair clear'; mark "degraded WAN (80 ms, 2% loss)" "$t" "$(now)"; }
wan-cut() { t=$(now); $SSH ops@10.110.0.1 'sudo wan-impair cut'; sleep 180; $SSH ops@10.110.0.1 'sudo wan-impair clear'; sleep 60; mark "site A partitioned for 3 min" "$t" "$(now)"; }
site-loss() { t=$(now); for id in $SITE_A_VMS; do qm stop "$id" --skiplock 1 & done; wait; sleep 180; mark "site A lost (power off)" "$t" "$(now)"; }
restore() {
  export DB_SIZE=5Gi DB_STORAGE_CLASS=longhorn S3_ENDPOINT=http://10.130.0.10:9000
  t=$(now); python3 tools/render.py k8s/restore/cluster.yaml | $KB apply -f -
  $KB -n c2 wait cluster/c2db-restore --for=condition=Ready --timeout=30m
  echo "{\"restore_seconds\": $(python3 -c "print(round($(now) - $t, 1))")}" | tee "$OUT/restore.json"
}
report() {
  scp ops@10.130.0.10:/opt/c2mgmt/run/probe.csv "$OUT/"
  client=$(sed -n 2p "$OUT/probe.csv" | cut -d, -f2)
  curl -s "http://10.120.0.80/orders/seqs?client=$client" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({d['client']: d['seqs']}))" >"$OUT/seqs.json"
  python3 tools/report.py --probe "$OUT/probe.csv" --markers "$OUT/markers.jsonl" --seqs "$OUT/seqs.json" --out "$OUT/report.md"
}

case "${1:-}" in
  all) for s in app-pod db-primary node-off disk-fail wan-degrade wan-cut site-loss restore; do "$s"; done; report ;;
  app-pod|db-primary|node-off|disk-fail|wan-degrade|wan-cut|site-loss|restore|report) "$1" ;;
  *) sed -n '2,22p' "$0"; exit 1 ;;
esac
