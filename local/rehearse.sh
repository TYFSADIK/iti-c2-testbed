#!/usr/bin/env bash
# Local rehearsal of the three-site design on one machine with Docker:
#   site A = k3d cluster (1 server + 2 agents), primary database (2 instances)
#   site B = k3d cluster (1 server + 1 agent), replica database streaming from A
#   site C = the management stack (witness/failover controller, DNS, S3 backup store, Prometheus, Grafana, Loki)
# then runs the failure scenarios and writes results/rehearsal/report.md.
#
#   ./local/rehearse.sh up        build everything (about 10 minutes)
#   ./local/rehearse.sh test      run the scenarios (about 15 minutes) and the report
#   ./local/rehearse.sh down      delete it all
#
# Needs: docker (with compose), k3d 5.7+, kubectl, python3. Scale: about 5 GB of RAM.
# What it cannot rehearse: Longhorn (needs iSCSI on real nodes), MetalLB (k3d uses
# its own service load balancer) and WireGuard; those are covered by the Proxmox build.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
RUN=$ROOT/results/rehearsal
KUBE=$RUN/kube
NET=c2net
CNPG_URL=https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.24/releases/cnpg-1.24.1.yaml
IMAGE=c2-services:local
export C2_NETWORK=$NET APP_IMAGE=$IMAGE RUN_DIR=$RUN KUBE_DIR=$KUBE

log() { printf '\n== %s\n' "$*"; }
ka() { kubectl --context k3d-site-a "$@"; }
kb() { kubectl --context k3d-site-b "$@"; }
now() { python3 -c 'import time; print(f"{time.time():.3f}")'; }
# GET a URL from inside the Docker network (the host cannot route to it on macOS)
nget() {
  docker exec c2mgmt-controller-1 python -c '
import sys, urllib.error, urllib.request
try:
    sys.stdout.write(urllib.request.urlopen(sys.argv[1], timeout=10).read().decode())
except urllib.error.HTTPError as e:  # a standby answers 503 with a JSON body
    sys.stdout.write(e.read().decode())' "$1"
}

secrets() {
  mkdir -p "$RUN"
  if [ ! -f "$RUN/.env" ]; then
    rnd() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20; }
    printf 'S3_ACCESS_KEY=c2backup\nS3_SECRET_KEY=%s\nDB_PASSWORD=%s\nGRAFANA_ADMIN_PASSWORD=%s\n' "$(rnd)" "$(rnd)" "$(rnd)" >"$RUN/.env"
    chmod 600 "$RUN/.env"
  fi
  # shellcheck disable=SC1091
  set -a
  # shellcheck source=/dev/null
  . "$RUN/.env"
  set +a
}

# docker compose for the site C stack; needs the site addresses once they exist
dc() {
  local sites="a=http://127.0.0.1:1,b=http://127.0.0.1:2" ips="a=127.0.0.1"
  if [ -f "$RUN/sites.env" ]; then
    # shellcheck source=/dev/null
    . "$RUN/sites.env"; sites="a=http://$A,b=http://$B"; ips="a=$A,b=$B"
  fi
  SITES=$sites SITE_IPS=$ips docker compose --env-file "$RUN/.env" -f stacks/mgmt/docker-compose.yml "$@"
}

node_ip() { docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" "$1"; }

wait_cluster() {  # context name
  log "waiting for database $2 on $1"
  for _ in $(seq 1 120); do
    phase=$(kubectl --context "$1" -n c2 get cluster "$2" -o jsonpath='{.status.phase}' 2>/dev/null || true)
    [ "$phase" = "Cluster in healthy state" ] && return 0
    sleep 5
  done
  echo "timed out; last phase: $phase"; return 1
}

up() {
  secrets
  log "network and image"
  docker network inspect $NET >/dev/null 2>&1 || docker network create --subnet 172.30.0.0/16 --ip-range 172.30.0.0/17 $NET
  docker build -q -t $IMAGE services
  mkdir -p "$KUBE"

  log "site A and site B clusters"
  # the server node runs only the control plane, so the node-loss test never removes the API server
  k3d cluster list site-a >/dev/null 2>&1 || k3d cluster create site-a --network $NET --servers 1 --agents 2 \
      --k3s-arg "--disable=traefik@server:0" --k3s-arg "--node-taint=role=control:NoSchedule@server:0" \
      --port "18081:80@loadbalancer" --port "15432:5432@loadbalancer" --wait
  k3d cluster list site-b >/dev/null 2>&1 || k3d cluster create site-b --network $NET --servers 1 --agents 1 \
      --k3s-arg "--disable=traefik@server:0" --port "18082:80@loadbalancer" --wait
  k3d image import -c site-a $IMAGE
  k3d image import -c site-b $IMAGE
  for s in a b; do  # kubeconfig the controller can use from inside the Docker network
    k3d kubeconfig get site-$s | sed "s#server: https://.*#server: https://k3d-site-$s-server-0:6443#" >"$KUBE/site-$s.yaml"
  done
  chmod 644 "$KUBE"/*.yaml

  log "site C management stack"
  rm -f "$RUN/sites.env"
  dc up -d s3 s3-init dns prometheus loki grafana

  log "CloudNativePG operator"
  for c in site-a site-b; do
    kubectl --context k3d-$c apply --server-side -f $CNPG_URL >/dev/null
  done
  for c in site-a site-b; do
    kubectl --context k3d-$c -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=300s
  done

  export DB_SIZE=1Gi DB_STORAGE_CLASS=local-path S3_ENDPOINT=http://172.30.255.20:9000 WITNESS_URL=http://172.30.255.10:8090 \
         APP_IMAGE=$IMAGE APP_VIP=""
  log "site A: primary database and app"
  for f in k8s/base/namespace.yaml k8s/base/secrets.yaml; do python3 tools/render.py $f | ka apply -f -; done
  DB_INSTANCES=3 python3 tools/render.py k8s/site-primary/cluster.yaml | ka apply -f -
  wait_cluster k3d-site-a c2db
  SITE=a python3 tools/render.py k8s/base/c2board.yaml | ka apply -f -

  log "site B: replica database and app"
  for f in k8s/base/namespace.yaml k8s/base/secrets.yaml; do python3 tools/render.py $f | kb apply -f -; done
  ka -n c2 get secret c2db-ca -o jsonpath='{.data.ca\.crt}' | base64 -d >"$RUN/ca.crt"
  ka -n c2 get secret c2db-replication -o jsonpath='{.data.tls\.crt}' | base64 -d >"$RUN/repl.crt"
  ka -n c2 get secret c2db-replication -o jsonpath='{.data.tls\.key}' | base64 -d >"$RUN/repl.key"
  kb -n c2 create secret generic site-a-ca --from-file=ca.crt="$RUN/ca.crt" --dry-run=client -o yaml | kb apply -f -
  kb -n c2 create secret tls site-a-replication --cert="$RUN/repl.crt" --key="$RUN/repl.key" --dry-run=client -o yaml | kb apply -f -
  rm -f "$RUN/repl.key"
  SITE_A_DB_HOST=$(node_ip k3d-site-a-serverlb) DB_INSTANCES=1 \
    python3 tools/render.py k8s/site-standby/cluster.yaml | tee "$KUBE/site-b-db.yaml" | kb apply -f -
  chmod 644 "$KUBE/site-b-db.yaml"
  wait_cluster k3d-site-b c2db
  SITE=b python3 tools/render.py k8s/base/c2board.yaml | kb apply -f -
  ka -n c2 rollout status deploy/c2board --timeout=180s
  kb -n c2 rollout status deploy/c2board --timeout=180s

  log "witness, failover controller and prober"
  # each site's front door: the k3d proxy in front of all of its nodes (MetalLB's role on Proxmox)
  A=$(node_ip k3d-site-a-serverlb); B=$(node_ip k3d-site-b-serverlb)
  echo "A=$A B=$B" >"$RUN/sites.env"
  PROBE_CLIENT="rehearsal-$(date +%s)" dc up -d controller prober
  sleep 15
  nget "http://$A/healthz"; echo; nget "http://$B/healthz"; echo
  log "up. Grafana: http://localhost:3000 (admin / GRAFANA_ADMIN_PASSWORD in $RUN/.env)"
}

mark() {  # scenario fault end [check_loss]
  printf '{"scenario": "%s", "fault": %s, "end": %s, "check_loss": %s}\n' "$1" "$2" "$3" "${4:-true}" >>"$RUN/markers.jsonl"
}

primary_pod() { kubectl --context "$1" -n c2 get pods -l cnpg.io/cluster=c2db,cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'; }
pod_node() { kubectl --context "$1" -n c2 get pod "$2" -o jsonpath='{.spec.nodeName}'; }

test_() {
  secrets
  # shellcheck disable=SC1091
  . "$RUN/sites.env"
  : >"$RUN/markers.jsonl"
  settle() { sleep "${1:-45}"; }

  log "0. baseline, 2 minutes"
  t=$(now); sleep 120; mark baseline "$t" "$(now)"

  log "1. one app pod killed at site A"
  t=$(now); ka -n c2 delete pod "$(ka -n c2 get pod -l app=c2board -o jsonpath='{.items[0].metadata.name}')" --wait=false
  settle 60; mark "app pod killed" "$t" "$(now)"

  log "2. database primary pod killed at site A (in-site failover)"
  t=$(now); ka -n c2 delete pod "$(primary_pod k3d-site-a)" --wait=false
  settle 90; mark "database primary pod killed" "$t" "$(now)"

  log "3. node hosting the site A primary powered off"
  pod=$(primary_pod k3d-site-a); node=$(pod_node k3d-site-a "$pod")
  t=$(now); docker stop "$node" >/dev/null
  settle 150; mark "node powered off ($node)" "$t" "$(now)"
  docker start "$node" >/dev/null; settle 60

  log "4. degraded WAN at site A: 80 ms delay, 2% loss for 2 minutes (traffic leaving the site only)"
  # shellcheck source=/dev/null
  . "$RUN/sites.env"
  others="172.30.255.0/24 $(node_ip k3d-site-b-server-0)/32 $(node_ip k3d-site-b-agent-0)/32 $(node_ip k3d-site-b-serverlb)/32"
  for n in k3d-site-a-server-0 k3d-site-a-agent-0 k3d-site-a-agent-1 k3d-site-a-serverlb; do
    cmds="tc qdisc add dev eth0 root handle 1: prio && tc qdisc add dev eth0 parent 1:3 handle 30: netem delay 80ms 10ms loss 2%"
    for o in $others; do cmds="$cmds && tc filter add dev eth0 protocol ip parent 1:0 prio 3 u32 match ip dst $o flowid 1:3"; done
    docker run --rm --net "container:$n" --cap-add NET_ADMIN nicolaka/netshoot:v0.13 sh -c "$cmds" >/dev/null
  done
  t=$(now); sleep 120; mark "degraded WAN (80 ms, 2% loss)" "$t" "$(now)"
  for n in k3d-site-a-server-0 k3d-site-a-agent-0 k3d-site-a-agent-1 k3d-site-a-serverlb; do
    docker run --rm --net "container:$n" --cap-add NET_ADMIN nicolaka/netshoot:v0.13 tc qdisc del dev eth0 root >/dev/null
  done
  settle 20

  log "5. witness lost: the site C controller stopped for 60 s"
  t=$(now); dc stop controller >/dev/null 2>&1; sleep 60; dc start controller >/dev/null 2>&1
  settle 40; mark "witness (site C) lost for 60 s" "$t" "$(now)"

  log "6. site A lost: every site A node cut off the network"
  t=$(now)
  for n in k3d-site-a-server-0 k3d-site-a-agent-0 k3d-site-a-agent-1 k3d-site-a-serverlb; do
    docker network disconnect $NET "$n" 2>/dev/null || true
  done
  settle 180; mark "site A lost (network cut)" "$t" "$(now)"

  log "7. restore drill: rebuild the database from the S3 backup at site B"
  export DB_SIZE=1Gi DB_STORAGE_CLASS=local-path S3_ENDPOINT=http://172.30.255.20:9000
  t=$(now); python3 tools/render.py k8s/restore/cluster.yaml | kb apply -f -
  wait_cluster k3d-site-b c2db-restore
  r=$(now)
  rows=$(kb -n c2 exec c2db-restore-1 -c postgres -- psql -d app -tAc "select count(*) from orders" 2>/dev/null || echo 0)
  echo "{\"restore_seconds\": $(python3 -c "print(round($r - $t, 1))"), \"rows\": ${rows:-0}}" >"$RUN/restore.json"
  cat "$RUN/restore.json"

  docker logs c2mgmt-controller-1 >"$RUN/controller.log" 2>&1 || true
  log "collect"
  dc stop prober
  client=$(head -2 "$RUN/probe.csv" | tail -1 | cut -d, -f2)
  nget "http://$B/orders/seqs?client=$client" | python3 -c "import json,sys; d=json.load(sys.stdin); json.dump({d['client']: d['seqs']}, open('$RUN/seqs.json','w'))"
  python3 tools/report.py --probe "$RUN/probe.csv" --markers "$RUN/markers.jsonl" --seqs "$RUN/seqs.json" --out "$RUN/report.md"
  log "reconnecting site A (it stays a demoted, fenced copy; see docs/05-failback.md)"
  for n in k3d-site-a-server-0 k3d-site-a-agent-0 k3d-site-a-agent-1 k3d-site-a-serverlb; do
    docker network connect $NET "$n" 2>/dev/null || true
  done
}

down() {
  secrets
  dc down -v 2>/dev/null || true
  rm -f "$RUN/sites.env" "$RUN/probe.csv" "$RUN/markers.jsonl" "$RUN/events.jsonl" "$RUN/state.json"
  k3d cluster delete site-a site-b || true
  docker network rm $NET 2>/dev/null || true
}

case "${1:-}" in
  up) up ;;
  test) test_ ;;
  down) down ;;
  *) echo "usage: $0 up|test|down"; exit 1 ;;
esac
