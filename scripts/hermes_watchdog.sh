#!/bin/sh
set -eu

OPS="${HERMES_OPS_DIR:-/volume1/docker/hermes/paotuan}"
LOG="$OPS/logs/watchdog.log"
LOCK_DIR="$OPS/watchdog.lock.d"
MAX_LOG_BYTES="${HERMES_WATCHDOG_MAX_LOG_BYTES:-1048576}"
MIN_FREE_KB="${HERMES_WATCHDOG_MIN_FREE_KB:-1048576}"

mkdir -p "$OPS/logs"

rotate_log() {
  if [ -f "$LOG" ]; then
    size="$(wc -c < "$LOG" 2>/dev/null || echo 0)"
    if [ "${size:-0}" -gt "$MAX_LOG_BYTES" ]; then
      mv "$LOG" "$LOG.1" 2>/dev/null || true
    fi
  fi
}

log() {
  rotate_log
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*" >> "$LOG"
}

http_ok() {
  url="$1"
  command -v curl >/dev/null 2>&1 && curl -fsS --max-time 5 "$url" >/dev/null 2>&1
}

proc_ok() {
  pattern="$1"
  ps -eo args 2>/dev/null | grep -F "$pattern" | grep -v grep >/dev/null 2>&1
}

disk_warning_if_needed() {
  free_kb="$(df -Pk "$OPS" 2>/dev/null | awk 'NR == 2 {print $4}')"
  if [ -n "${free_kb:-}" ] && [ "$free_kb" -lt "$MIN_FREE_KB" ]; then
    log "warning: low disk space at $OPS free_kb=$free_kb min_free_kb=$MIN_FREE_KB"
  fi
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT INT TERM

needs_start=0
reasons=""

if ! proc_ok "hermes gateway run"; then
  needs_start=1
  reasons="${reasons} gateway_process"
fi

if ! http_ok "http://127.0.0.1:8766/health"; then
  needs_start=1
  reasons="${reasons} webhook_health"
fi

if ! http_ok "http://127.0.0.1:8767/health"; then
  needs_start=1
  reasons="${reasons} coder_bridge_health"
fi

if ! http_ok "http://127.0.0.1:9119/"; then
  needs_start=1
  reasons="${reasons} dashboard_health"
fi

if [ "$needs_start" -eq 1 ]; then
  disk_warning_if_needed
  log "Hermes unhealthy:${reasons}; starting services"
  if sh "$OPS/bin/start_services.sh" >> "$LOG" 2>&1; then
    log "start_services.sh completed"
  else
    rc=$?
    log "start_services.sh failed rc=$rc"
    exit "$rc"
  fi
fi
