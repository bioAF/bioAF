#!/usr/bin/env bash
# Smoke-test a built frontend image directly and through the shipped nginx ingress (docker/nginx.conf).
#
# The image optimizer is disabled (frontend/next.config.js, images.unoptimized): /_next/image must be refused
# (404) by the packaged Next server and through nginx alike, while static images and app pages still load.
# Uses only a public asset path as the optimizer's url parameter and a throwaway self-signed certificate; it prints
# request paths and status codes, never response bodies.
#
#   scripts/ci/frontend_image_smoke.sh <frontend-image>
set -euo pipefail

image=${1:?usage: frontend_image_smoke.sh <frontend-image>}
repo=$(cd "$(dirname "$0")/../.." && pwd)
run=bioaf-smoke-$$
work=$(mktemp -d)

cleanup() {
  docker rm -f "$run-frontend" "$run-nginx" >/dev/null 2>&1 || true
  docker network rm "$run" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

docker network create "$run" >/dev/null
# nginx resolves every upstream at start; the frontend container answers to all their names. No backend or
# notebook gateway route is checked.
docker run -d --name "$run-frontend" --network "$run" --network-alias frontend --network-alias backend \
  --network-alias notebook-gateway \
  -p 127.0.0.1::3000 "$image" >/dev/null

mkdir -p "$work/certs"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -keyout "$work/certs/tls.key" -out "$work/certs/tls.crt" >/dev/null 2>&1
chmod 0644 "$work/certs/tls.key"
mkdir -p "$work/update-gate"
# docker/nginx.conf is a template, rendered at start exactly as docker/docker-compose.yml does: only BIOAF_
# variables (the edge token) are substituted, and the update-gate directory is mounted (empty: launches open).
docker run -d --name "$run-nginx" --network "$run" -p 127.0.0.1::443 \
  -e NGINX_ENVSUBST_FILTER='^BIOAF_' -e BIOAF_EDGE_TOKEN=smoke-edge-token \
  -v "$repo/docker/nginx.conf:/etc/nginx/templates/default.conf.template:ro" \
  -v "$work/update-gate:/etc/nginx/update-gate:ro" -v "$work/certs:/etc/nginx/certs:ro" \
  nginx:alpine >/dev/null

direct="http://$(docker port "$run-frontend" 3000/tcp | head -n 1)"
ingress="https://$(docker port "$run-nginx" 443/tcp | head -n 1)"

status() { curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "$1" || echo 000; }

ready=0
for _ in $(seq 1 60); do
  if [ "$(status "$direct/login")" = 200 ] && [ "$(status "$ingress/login")" = 200 ]; then ready=1; break; fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  echo "::error::the frontend did not become ready directly and through nginx"
  docker logs --tail 20 "$run-nginx" 2>&1 | sed 's/^/nginx: /'
  exit 1
fi

optimizer="/_next/image?url=%2Fgetting-started%2Fdashboard.png&w=64&q=75"
# An absolute URL on a reserved name: refused the same way, before any fetch is attempted.
remote="/_next/image?url=https%3A%2F%2Fexample.invalid%2Fa.png&w=64&q=75"
failed=0
expect() {
  local base=$1 label=$2 path=$3 want=$4 got
  got=$(status "$base$path")
  if [ "$got" = "$want" ]; then
    echo "ok   $label $path -> $got"
  else
    echo "::error::$label $path returned $got, expected $want"
    failed=1
  fi
}

for target in "direct $direct" "ingress $ingress"; do
  label=${target%% *}
  base=${target#* }
  expect "$base" "$label" "$optimizer" 404
  expect "$base" "$label" "$remote" 404
  expect "$base" "$label" /getting-started/dashboard.png 200
  expect "$base" "$label" /bioAF-logo.svg 200
  expect "$base" "$label" /login 200
done
exit $failed
