#!/usr/bin/env bash
# Generate secrets/lab.env (once). Re-running keeps existing values.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p secrets/kube && chmod 700 secrets
f=secrets/lab.env
touch "$f" && chmod 600 "$f"
rnd() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-24}"; }
add() { grep -q "^$1=" "$f" || echo "$1=$2" >>"$f"; }
add DB_PASSWORD "$(rnd)"
add S3_ACCESS_KEY c2backup
add S3_SECRET_KEY "$(rnd 32)"
add GRAFANA_ADMIN_PASSWORD "$(rnd)"
add K3S_TOKEN_A "$(rnd 48)"
add K3S_TOKEN_B "$(rnd 48)"
echo "secrets in $f"
