#!/bin/sh
set -eu

HERMES_ROOT="${HERMES_ROOT:-/volume1/docker/hermes}"
OPS="$HERMES_ROOT/paotuan"
COMPOSE="$OPS/deploy/hermes/docker-compose.yml"
MIN_FREE_KB="${HERMES_CONTAINER_MIN_FREE_KB:-5242880}"
DOCKER="${DOCKER:-/usr/local/bin/docker}"
export HERMES_UID="${HERMES_UID:-$(id -u)}"
export HERMES_GID="${HERMES_GID:-$(id -g)}"
export HERMES_USER="${HERMES_USER:-$(id -un 2>/dev/null || echo hermes)}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*"
}

free_kb() {
  df -Pk "$HERMES_ROOT" | awk 'NR == 2 {print $4}'
}

compose_cmd() {
  if "$DOCKER" compose version >/dev/null 2>&1; then
    "$DOCKER" compose -f "$COMPOSE" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -f "$COMPOSE" "$@"
  else
    log "docker compose is not available"
    return 127
  fi
}

healthcheck() {
  python3 - <<'PY'
import urllib.request
for url in ("http://127.0.0.1:8767/health", "http://127.0.0.1:8766/health", "http://127.0.0.1:9119/"):
    with urllib.request.urlopen(url, timeout=8) as resp:
        if resp.status < 200 or resp.status >= 300:
            raise SystemExit(f"{url} returned {resp.status}")
        print(url, resp.status)
PY
}

prepare_identity_files() {
  runtime_dir="$HERMES_ROOT/runtime"
  mkdir -p "$runtime_dir"
  "$DOCKER" run --rm node:22-bookworm cat /etc/passwd > "$runtime_dir/passwd.tmp"
  "$DOCKER" run --rm node:22-bookworm cat /etc/group > "$runtime_dir/group.tmp"
  if ! awk -F: -v uid="$HERMES_UID" '$3 == uid { found=1 } END { exit found ? 0 : 1 }' "$runtime_dir/passwd.tmp"; then
    printf '%s:x:%s:%s::%s/home:/bin/sh\n' "$HERMES_USER" "$HERMES_UID" "$HERMES_GID" "$HERMES_ROOT" >> "$runtime_dir/passwd.tmp"
  fi
  if ! awk -F: -v gid="$HERMES_GID" '$3 == gid { found=1 } END { exit found ? 0 : 1 }' "$runtime_dir/group.tmp"; then
    printf '%s:x:%s:\n' "${HERMES_GROUP:-users}" "$HERMES_GID" >> "$runtime_dir/group.tmp"
  fi
  mv "$runtime_dir/passwd.tmp" "$runtime_dir/passwd"
  mv "$runtime_dir/group.tmp" "$runtime_dir/group"
  chmod 0644 "$runtime_dir/passwd" "$runtime_dir/group"
  log "prepared container identity files for ${HERMES_USER} (${HERMES_UID}:${HERMES_GID})"
}

preflight() {
  if [ ! -f "$COMPOSE" ]; then
    log "missing compose file: $COMPOSE"
    return 1
  fi
  free="$(free_kb)"
  log "free space: ${free} KiB"
  if [ "$free" -lt "$MIN_FREE_KB" ]; then
    log "refusing to build/start Hermes containers: free space below ${MIN_FREE_KB} KiB"
    return 1
  fi
  if [ ! -x "$HERMES_ROOT/install/hermes-agent/venv/bin/hermes" ]; then
    log "missing Hermes host venv: $HERMES_ROOT/install/hermes-agent/venv/bin/hermes"
    return 1
  fi
  if ! "$DOCKER" image inspect node:22-bookworm >/dev/null 2>&1; then
    log "missing runtime image node:22-bookworm"
    return 1
  fi
  prepare_identity_files
  compose_cmd config >/dev/null
  log "preflight ok"
}

stop_host_processes() {
  log "stopping host Hermes processes"
  pkill -f "[h]ermes-watchdog-loop" 2>/dev/null || true
  pkill -f "[h]ermes_coder_bridge.py" 2>/dev/null || true
  pkill -f "[p]aotuan_webhook.py" 2>/dev/null || true
  pkill -f "[h]ermes dashboard --host" 2>/dev/null || true
  if command -v hermes >/dev/null 2>&1; then
    hermes gateway stop --all >/dev/null 2>&1 || true
  fi
  pkill -f "[h]ermes gateway run" 2>/dev/null || true
}

case "${1:-preflight}" in
  preflight)
    preflight
    ;;
  up)
    preflight
    log "runtime image is already present; no local Hermes image build required"
    stop_host_processes
    if ! compose_cmd up -d; then
      log "container startup failed; rolling back to host services"
      sh "$OPS/bin/start_services.sh" || true
      exit 1
    fi
    sleep 8
    if ! healthcheck; then
      log "container healthcheck failed; rolling back to host services"
      compose_cmd down || true
      sh "$OPS/bin/start_services.sh" || true
      exit 1
    fi
    log "Hermes containers are healthy"
    ;;
  down)
    compose_cmd down
    ;;
  status)
    compose_cmd ps
    healthcheck
    ;;
  *)
    echo "usage: $0 {preflight|up|down|status}" >&2
    exit 2
    ;;
esac
