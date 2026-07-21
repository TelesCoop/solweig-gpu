#!/usr/bin/env bash
# Runs pipeline/02_run_solweig.py (solweig-gpu) and pipeline/02b_run_solweig_umep.py
# (solweig/umep-rust) back-to-back — never concurrently — and records wall-clock
# time, peak RAM and GPU memory for each, then builds a comparison report.
#
# Sequential on purpose: this machine already runs another GPU job (check
# `nvidia-smi` / `free -h`), and running both benchmarks at once would make
# the timings meaningless (contention) and risks OOM-killing something.
set -uo pipefail

cd "$(dirname "$0")/.."

TS=$(date +%Y%m%d_%H%M%S)
OUTDIR="benchmarks/runs/${TS}"
mkdir -p "$OUTDIR"

MIN_FREE_MB=${MIN_FREE_MB:-3000}   # abort a stage if available RAM drops below this

log() { echo "[$(date -Is)] $*"; }

snapshot_resources() {
  local tag="$1"
  free -h > "$OUTDIR/mem_${tag}.txt"
  nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv \
    > "$OUTDIR/gpu_${tag}.txt" 2>/dev/null || echo "nvidia-smi unavailable" > "$OUTDIR/gpu_${tag}.txt"
}

check_resources_or_abort() {
  local avail_mb
  avail_mb=$(free -m | awk '/^Mem:/{print $7}')
  log "available RAM: ${avail_mb} MiB (threshold ${MIN_FREE_MB} MiB)"
  if [ "$avail_mb" -lt "$MIN_FREE_MB" ]; then
    log "ABORT: available RAM below threshold — refusing to start a new stage" \
        "to avoid OOM-killing the other job running on this box."
    return 1
  fi
  return 0
}

run_stage() {
  local name="$1"; shift
  log "=== [$name] pre-flight check ==="
  if ! check_resources_or_abort; then
    echo "aborted: low memory" > "$OUTDIR/${name}.status"
    return 1
  fi
  snapshot_resources "before_${name}"

  log "=== [$name] starting: $* ==="
  local start end rc
  start=$(date +%s)
  /usr/bin/time -v "$@" > "$OUTDIR/${name}.log" 2> "$OUTDIR/${name}.time"
  rc=$?
  end=$(date +%s)

  echo "$((end - start))" > "$OUTDIR/${name}.duration_s"
  echo "$rc" > "$OUTDIR/${name}.status"
  snapshot_resources "after_${name}"
  log "=== [$name] finished rc=${rc} duration=$((end - start))s ==="
  return $rc
}

log "Comparison run starting, output dir: $OUTDIR"
snapshot_resources "start"

run_stage solweig_gpu  uv run python pipeline/02_run_solweig.py
rc_gpu=$?

run_stage solweig_umep uv run python pipeline/02b_run_solweig_umep.py
rc_umep=$?

log "Generating report..."
uv run python benchmarks/report.py "$OUTDIR"

log "Done. rc_gpu=${rc_gpu} rc_umep=${rc_umep}. Report: $OUTDIR/report.md"
